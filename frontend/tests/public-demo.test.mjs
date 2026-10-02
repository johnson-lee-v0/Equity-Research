import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const require = createRequire(import.meta.url)
const { build } = createRequire(require.resolve('vite'))('esbuild')
async function compile(relativePath) {
  const bundle = await build({
    entryPoints: [fileURLToPath(new URL(relativePath, import.meta.url))],
    bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external', jsx: 'automatic',
    define: { 'import.meta.env.BASE_URL': '"./"' }, loader: { '.css': 'empty' }, logLevel: 'silent', metafile: true,
  })
  const module = { exports: {} }
  new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(require, module, module.exports)
  return { ...module.exports, inputs: Object.keys(bundle.metafile.inputs) }
}
const data = await compile('../src/demo/syntheticData.ts')
const view = await compile('../src/demo/SyntheticDemo.tsx')
const render = component => renderToStaticMarkup(React.createElement(component))
const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9)

test('one-year arithmetic responds to downside and upside without pretending to predict returns', () => {
  close(data.calculateExample(5, 16).future, 50.4)
  close(data.calculateExample(-20, 12).future, 28.8)
  close(data.calculateExample(15, 20).future, 69)
  close(data.calculateExample(0, 16).future, 48)
  close(data.calculateExample(-100, 12).future, 0)
  close(data.reviewThreshold, 36)
  for (const [growth, multiple] of [[NaN, 16], [5, Infinity], [-101, 16], [5, -1]]) assert.equal(data.calculateExample(growth, multiple), null)
})

test('all teaching notes are original fictional material, with closed graph links and no third-party provenance claims', () => {
  const ids = new Set(data.syntheticNotes.map(note => note.id))
  assert.equal(data.syntheticGraph.total_nodes, ids.size)
  assert.deepEqual(data.syntheticGraph.tickers, [])
  for (const note of data.syntheticNotes) {
    assert.equal(note.status, 'fictional_teaching_material')
    assert.equal(note.source_url, undefined)
    assert.equal(note.ticker, null)
    assert.ok(note.links.every(link => ids.has(link.target)))
  }
  assert.ok(data.syntheticGraph.edges.every(edge => ids.has(edge.source) && ids.has(edge.target)))
  assert.equal(data.inputs.length, 1, 'Teaching inputs import no real-company data or source archive')
})

test('entry displays advice, total-loss, staleness and static-fictional notices without opening disclosures', () => {
  const html = render(view.default)
  const visible = html.replace(/<details\b[^>]*>[\s\S]*?<\/details>/gu, '')
  for (const pattern of [/Education and research only/, /not personalized investment advice/, /total loss/, /wrong|errors/, /stale/, /Independently verify/, /Fictional teaching demo/, /Static examples/, /no live research/, /LICENSE\.txt/, /THIRD_PARTY_NOTICES\.txt/]) assert.match(visible, pattern)
  assert.doesNotMatch(html, /market-news\.json|Latest headlines|751\.66|Muse|Q2 FY2026/)
  assert.match(html, /hosting providers may log requests/)
})

test('financial scenarios display limitations alongside the controls and decision exercise', () => {
  for (const component of [view.PricingExample, view.DecisionExample]) {
    const html = render(component)
    const visible = html.replace(/<details\b[^>]*>[\s\S]*?<\/details>/gu, '')
    assert.match(visible, /All company figures and prices here are invented/)
    assert.match(visible, /not probabilities or a worst-case limit/)
    assert.match(visible, /entire value/)
    assert.match(visible, /Independently verify/)
  }
  const html = render(view.PricingExample)
  assert.match(html, /Annual EPS growth assumption/)
  assert.match(html, /P\/E multiple assumption/)
  assert.match(html, /\$50\.40/)
  assert.match(html, /No dividends, fees, taxes, debt changes or dilution/)
})

test('earnings table identifies all numbers as invented and does not fabricate real management speech', () => {
  const html = render(view.EarningsExample)
  assert.match(html, /Original fictional teaching inputs/)
  assert.match(html, /no real company, filing or earnings call/)
  assert.match(html, /104/)
  assert.match(html, /12\.5%/)
  assert.match(html, /14.*9/)
  assert.doesNotMatch(html, /<blockquote|Zuckerberg|META|Stock Analysis/)
})

test('public dependency closure excludes the local workspace, retired real-company data and news fetching', async () => {
  const bundle = await build({
    entryPoints: [fileURLToPath(new URL('../src/main.tsx', import.meta.url))],
    bundle: true, write: false, platform: 'browser', format: 'esm', packages: 'external', jsx: 'automatic',
    define: { __PUBLIC_DEMO__: 'true', 'import.meta.env.BASE_URL': '"./"' },
    loader: { '.css': 'empty' }, logLevel: 'silent', metafile: true,
  })
  const inputs = Object.keys(bundle.metafile.inputs).map(path => path.replaceAll('\\', '/'))
  assert.ok(inputs.some(path => path.endsWith('/demo/SyntheticDemo.tsx')))
  assert.ok(inputs.some(path => path.endsWith('/panels/MemoryGraph3D.tsx')), 'Interactive notebook remains available')
  assert.equal(inputs.some(path => /\/(?:App|DemoApp|MarketNews|MetaPricing|metaValuation|metaTrends|metaEarningsCall|demoData)\.[jt]sx?$/.test(path)), false)
  assert.equal(inputs.some(path => /(?:^|\/)(?:data|runtime|private|backups)\//.test(path)), false)
  const code = bundle.outputFiles.map(file => file.text).join('\n')
  assert.doesNotMatch(code, /new EventSource\(|\/api\/runs|\/api\/memory\/sync|\/api\/office|market-news\.json|fetch\(/)
  assert.doesNotMatch(code, /localStorage|sessionStorage|sendBeacon|googletagmanager/)
})
