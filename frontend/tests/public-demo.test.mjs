import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const require = createRequire(import.meta.url)
const { build } = createRequire(require.resolve('vite'))('esbuild')
const bundle = await build({
  entryPoints: [fileURLToPath(new URL('../src/demo/demoData.ts', import.meta.url))],
  bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external',
  logLevel: 'silent', metafile: true,
})
const compiled = { exports: {} }
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(require, compiled, compiled.exports)
const { demoPrice, capex, demoNotes, demoGraph, questions, illustrativeHistory, latestFinancials, DEMO_AS_OF, EARNINGS_REPORTED_AT, EARNINGS_PERIOD_END, MUSE_RELEASED_AT, MUSE_RELEASE } = compiled.exports
const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 0.000001, `${actual} does not match independently calculated ${expected}`)

test('12 months means one growth period, while today applies none', () => {
  // ExampleCo EPS of $4.80 at 22× is $105.60 today. With 10% growth,
  // next-year EPS is $5.28; the year after that is $5.808.
  close(demoPrice('P/E', 10, 22, 0), 105.6)
  close(demoPrice('P/E', 10, 22, 12), 116.16)
  close(demoPrice('P/E', 10, 22, 24), 127.776)
  close(demoPrice('P/E', -20, 22, 12), 84.48)
})

test('revenue, enterprise value and equity NAV produce distinct per-share prices', () => {
  // Independent monetary reconciliation of the authored ExampleCo inputs.
  // $329.28bn equity / 1.86bn shares; $324.80bn equity after EV bridge;
  // $188.945bn equity NAV after the 1.15× assumption.
  close(demoPrice('P/S', 0, 2.4), 177.03225806451613)
  close(demoPrice('EV/EBITDA', 0, 11), 174.6236559139785)
  close(demoPrice('P/NAV', 0, 1.15), 101.58333333333333)
  // A 10% EBITDA increase adds $31.46bn of enterprise value. The existing
  // cash/debt/other-claims bridge is unchanged and must not grow a second time.
  close(demoPrice('EV/EBITDA', 10, 11, 12), 191.53763440860217)
})

test('every actual capex bar and factual research question links to issuer material', () => {
  assert.equal(capex.frequency, 'quarterly')
  assert.equal(capex.points.find(point => point.period === 'Q3 FY2025').value, 19.374)
  assert.equal(capex.points.some(point => point.value === 50.078), false, 'Year-to-date spending is not a quarterly bar')
  for (const point of capex.points) {
    assert.equal(point.kind, 'actual')
    const url = new URL(point.url)
    assert.equal(url.protocol, 'https:')
    assert.equal(url.hostname, 's21.q4cdn.com')
    assert.match(url.pathname, /^\/399680738\/files\/doc_financials\/2026\/q2\//)
    assert.ok(point.published_at)
    assert.match(point.source_method, /issuer/)
  }
  for (const question of questions.filter(question => question.source)) {
    assert.ok(['investor.atmeta.com', 's21.q4cdn.com', 'about.fb.com'].includes(new URL(question.source).hostname))
  }
})

test('generated multiple history is explicitly fictional and does not relabel book equity as NAV', () => {
  const histories = illustrativeHistory().historical_multiples
  assert.deepEqual(Object.keys(histories), ['P/E', 'P/S', 'EV/EBITDA', 'P/book'])
  for (const history of Object.values(histories)) {
    assert.match(history.basis, /FICTIONAL EXAMPLECO/)
    assert.match(history.basis, /Not Meta trading history/)
    assert.match(history.coverage_note, /synthetic/)
    assert.equal(history.points.length, 60)
    assert.ok(history.points.every(point => Number.isFinite(point.multiple)))
  }
})

test('public notebook links stay inside its authored demo graph and issuer sources', () => {
  const ids = new Set(demoNotes.map(note => note.id))
  assert.equal(ids.size, demoNotes.length)
  assert.equal(demoGraph.total_nodes, demoNotes.length)
  for (const note of demoNotes) {
    assert.equal(note.frontmatter.namespace, 'demo')
    assert.match(note.path, /^demo\//)
    assert.ok(note.links.every(link => ids.has(link.target)))
    if (note.source_url) assert.ok(['investor.atmeta.com', 's21.q4cdn.com', 'about.fb.com'].includes(new URL(note.source_url).hostname))
  }
  for (const edge of demoGraph.edges) {
    assert.ok(ids.has(edge.source))
    assert.ok(ids.has(edge.target))
  }
  assert.equal(Object.keys(bundle.metafile.inputs).length, 1, 'Authored public data has no runtime source/ledger imports')
})

test('public entry build excludes the local workspace, event stream and private data paths', async () => {
  const publicBuild = await build({
    entryPoints: [fileURLToPath(new URL('../src/main.tsx', import.meta.url))],
    bundle: true, write: false, platform: 'browser', format: 'esm', packages: 'external', jsx: 'automatic',
    define: { __PUBLIC_DEMO__: 'true', 'import.meta.env.BASE_URL': '"./"' },
    loader: { '.css': 'empty' }, logLevel: 'silent', metafile: true,
  })
  const inputs = Object.keys(publicBuild.metafile.inputs).map(path => path.replaceAll('\\', '/'))
  assert.ok(inputs.some(path => path.endsWith('/demo/DemoApp.tsx')))
  assert.equal(inputs.some(path => path.endsWith('/src/App.tsx')), false, 'The local workspace is not a public build dependency')
  assert.equal(inputs.some(path => /(?:^|\/)(?:data|runtime|private|backups)\//.test(path)), false)
  const code = publicBuild.outputFiles.map(file => file.text).join('\n')
  assert.doesNotMatch(code, /new EventSource\(|\/api\/runs|\/api\/memory\/sync|\/api\/office/)
  assert.match(code, /market-news\.json/)
})


test('financial snapshot uses the reported June quarter and preserves quarter-only cash arithmetic', () => {
  assert.equal(DEMO_AS_OF, '2026-09-26')
  assert.equal(EARNINGS_REPORTED_AT, '2026-07-29')
  assert.equal(EARNINGS_PERIOD_END, '2026-06-30')
  assert.equal(latestFinancials.period, 'Q2 FY2026')
  assert.equal(latestFinancials.revenue, 60.801)
  assert.equal(latestFinancials.operatingMarginPercent, 31)
  assert.equal(latestFinancials.priorOperatingMarginPercent, 43)
  assert.deepEqual(capex.points.map(point => point.value), [13.692, 17.012, 19.374, 22.137, 19.84, 31.078])
  assert.equal(capex.points.at(-1).period, 'Q2 FY2026')
  assert.equal(capex.points.some(point => point.period === 'Q3 FY2026'), false)
  close(latestFinancials.cashPpe + latestFinancials.financeLeasePrincipal, 31.078)
  close(latestFinancials.operatingCashFlow - latestFinancials.capex, 0.784)
  assert.equal(latestFinancials.yearToDateCapex, 50.918)
  assert.equal(capex.points.some(point => point.value === 50.918), false)
  assert.deepEqual(latestFinancials.annualCapexGuidance, [130, 145])
})

test('Muse is a later sourced catalyst and an unresolved economics question', () => {
  assert.equal(MUSE_RELEASED_AT, '2026-09-08')
  assert.ok(MUSE_RELEASED_AT > EARNINGS_PERIOD_END)
  assert.ok(MUSE_RELEASED_AT <= DEMO_AS_OF)
  assert.equal(MUSE_RELEASE, 'https://about.fb.com/news/2026/09/introducing-muse-personal-ai-agent/')
  assert.ok(questions.some(question => question.source === MUSE_RELEASE && /after Q2/.test(question.answer)))
  assert.ok(demoNotes.some(note => note.id === 'muse' && note.source_url === MUSE_RELEASE))
  assert.ok(demoNotes.some(note => note.kind === 'gap' && /Muse/.test(note.title)))
})

test('rendered earnings and Muse cards distinguish actuals, guidance and interpretation with sources closed', async () => {
  const ui = await build({
    entryPoints: [fileURLToPath(new URL('../src/demo/DemoApp.tsx', import.meta.url))],
    bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external', jsx: 'automatic',
    define: { 'import.meta.env.BASE_URL': '"./"' },
    loader: { '.css': 'empty' }, logLevel: 'silent',
  })
  const module = { exports: {} }
  new Function('require', 'module', 'exports', ui.outputFiles[0].text)(require, module, module.exports)
  const html = renderToStaticMarkup(React.createElement(module.exports.EarningsSnapshot, { onNext() {} }))
  assert.match(html, /Sales rose\. Operating profit fell\./)
  assert.ok(questions.some(question => /2\.40/.test(question.context ?? '') && /1\.18/.test(question.context ?? '')))
  assert.match(html, /60\.801/)
  assert.match(html, /31\.078/)
  assert.match(html, /0\.784/)
  assert.match(html, /130–145bn/)
  assert.match(html, /management’s forecast/)
  assert.match(html, /launch cannot explain Q2 growth/)
  assert.match(html, /Thesis interpretations/)
  assert.match(html, /What would prove it/)
  assert.match(html, /Company claims and launch source/)
  assert.doesNotMatch(html, /<details[^>]*\bopen(?:[ =]|>)/)
  assert.match(html, /Review the five questions/)
  const home = renderToStaticMarkup(React.createElement(module.exports.default))
  assert.match(home, /Data checked September 26, 2026/)
  assert.doesNotMatch(home, /Historical example|October 29, 2025/)
})
