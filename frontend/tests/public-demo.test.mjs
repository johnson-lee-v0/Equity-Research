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
const { capex, demoNotes, demoGraph, questions, latestFinancials, DEMO_AS_OF, EARNINGS_REPORTED_AT, EARNINGS_PERIOD_END, MUSE_RELEASED_AT, MUSE_RELEASE } = compiled.exports
const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 0.000001, `${actual} does not match independently calculated ${expected}`)

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
    assert.ok(['investor.atmeta.com', 's21.q4cdn.com', 'about.fb.com', 'stockanalysis.com'].includes(new URL(question.source).hostname))
  }
})

test('public notebook links stay inside its sourced META graph and cited sources', () => {
  const ids = new Set(demoNotes.map(note => note.id))
  assert.equal(ids.size, demoNotes.length)
  assert.equal(demoGraph.total_nodes, demoNotes.length)
  for (const note of demoNotes) {
    assert.equal(note.frontmatter.namespace, 'demo')
    assert.match(note.path, /^demo\//)
    assert.ok(note.links.every(link => ids.has(link.target)))
    if (note.source_url) assert.ok(['investor.atmeta.com', 's21.q4cdn.com', 'about.fb.com', 'stockanalysis.com'].includes(new URL(note.source_url).hostname))
  }
  for (const edge of demoGraph.edges) {
    assert.ok(ids.has(edge.source))
    assert.ok(ids.has(edge.target))
  }
  assert.ok(Object.keys(bundle.metafile.inputs).every(path => /(?:demoData|metaEarningsCall|metaValuation|metaTrends)\.ts$/.test(path)), 'Public data depends only on explicitly authored, source-bound modules')
})

test('every graph connection is navigable in the text notebook and note bodies render as paragraphs', async () => {
  for (const edge of demoGraph.edges) {
    assert.ok(demoNotes.find(note => note.id === edge.source).links.some(link => link.target === edge.target), edge.source + ' can open ' + edge.target)
    assert.ok(demoNotes.find(note => note.id === edge.target).links.some(link => link.target === edge.source), edge.target + ' can open ' + edge.source)
  }
  const ui = await build({
    entryPoints: [fileURLToPath(new URL('../src/panels/Memory.tsx', import.meta.url))],
    bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external', jsx: 'automatic',
    loader: { '.css': 'empty' }, logLevel: 'silent',
  })
  const module = { exports: {} }
  new Function('require', 'module', 'exports', ui.outputFiles[0].text)(require, module, module.exports)
  for (const note of demoNotes) {
    const html = renderToStaticMarkup(React.createElement(module.exports.MemoryDetail, {
      note, node: note, loading: false, error: '', onSelect() {}, onRetry() {},
    }))
    assert.ok(html.includes(renderToStaticMarkup(React.createElement('p', null, note.excerpt))), note.id + ' has a readable paragraph')
    assert.doesNotMatch(note.markdown, /\\n/, 'Markdown uses actual paragraph breaks')
  }
})

test('five questions preserve all source links without calling a market provider company material', async () => {
  const ui = await build({
    entryPoints: [fileURLToPath(new URL('../src/demo/DemoApp.tsx', import.meta.url))],
    bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external', jsx: 'automatic',
    define: { 'import.meta.env.BASE_URL': '"./"' }, loader: { '.css': 'empty' }, logLevel: 'silent',
  })
  const state = ['Research', '', '', true, 1, false, false]
  let cursor = 0
  const hooks = { ...React, useState: () => [state[cursor++], () => {}], useEffect() {}, useRef: () => ({ current: null }) }
  const module = { exports: {} }
  new Function('require', 'module', 'exports', ui.outputFiles[0].text)(name => name === 'react' ? hooks : require(name), module, module.exports)
  const html = renderToStaticMarkup(React.createElement(module.exports.default))
  assert.match(html, /Five questions, in plain language/)
  assert.equal((html.match(/Read the cited source/g) ?? []).length, questions.length)
  assert.doesNotMatch(html, /Read the company material/)
  for (const question of questions) assert.ok(html.includes(`href="${question.source}"`))
  assert.match(html, /href="https:\/\/stockanalysis\.com\/stocks\/meta\/history\/"/)
})

test('public entry build excludes the local workspace, event stream and private data paths', async () => {
  const publicBuild = await build({
    entryPoints: [fileURLToPath(new URL('../src/main.tsx', import.meta.url))],
    bundle: true, write: false, platform: 'browser', format: 'esm', packages: 'external', jsx: 'automatic',
    define: { __PUBLIC_DEMO__: 'true', 'import.meta.env.BASE_URL': '"./"' },
    loader: { '.css': 'empty' }, logLevel: 'silent', metafile: true,
  })
  const inputs = Object.keys(publicBuild.metafile.inputs).map(path => path.replaceAll('\\', '/'))
  assert.ok(inputs.some(path => path.endsWith('/demo/AcknowledgedDemo.tsx')))
  assert.ok(inputs.some(path => path.endsWith('/demo/DemoApp.tsx')))
  assert.ok(inputs.some(path => path.endsWith('/panels/MemoryGraph3D.tsx')), 'The full interactive notebook remains available')
  assert.equal(inputs.some(path => path.endsWith('/demo/SyntheticDemo.tsx')), false)
  assert.equal(inputs.some(path => path.endsWith('/src/App.tsx')), false, 'The local workspace is not a public build dependency')
  assert.equal(inputs.some(path => /(?:^|\/)(?:data|runtime|private|backups)\//.test(path)), false)
  const code = publicBuild.outputFiles.map(file => file.text).join('\n')
  assert.doesNotMatch(code, /new EventSource\(|\/api\/runs|\/api\/memory\/sync|\/api\/office/)
  assert.doesNotMatch(code, /market-news\.json|fetch\(/)
  assert.doesNotMatch(code, /localStorage|sessionStorage|sendBeacon|googletagmanager/)
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
  assert.doesNotMatch(html + home, /ExampleCo|Generated sample values|Fictional allocation|Illustrative intake/)
  assert.match(html, /META earnings call analysis/)
  assert.match(html, /Speaker context and original wording/)
  assert.doesNotMatch(home, /Historical example|October 29, 2025/)
})

async function compileView(relativePath) {
  const ui = await build({
    entryPoints: [fileURLToPath(new URL(relativePath, import.meta.url))],
    bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external', jsx: 'automatic',
    define: { 'import.meta.env.BASE_URL': '"./"' }, loader: { '.css': 'empty' }, logLevel: 'silent',
  })
  const module = { exports: {} }
  new Function('require', 'module', 'exports', ui.outputFiles[0].text)(require, module, module.exports)
  return module.exports
}

test('restored entry keeps visible risk, privacy and dependency-license notices', async () => {
  const view = await compileView('../src/demo/DemoApp.tsx')
  const html = renderToStaticMarkup(React.createElement(view.default))
  const visible = html.replace(/<details\b[^>]*>[\s\S]*?<\/details>/gu, '')
  for (const pattern of [/Education and research only/, /not personalized investment advice/, /total loss/, /errors/, /stale/, /Independently verify/, /Static META research/, /no live research/, /LICENSE\.txt/, /THIRD_PARTY_NOTICES\.txt/, /hosting providers may log requests/]) assert.match(visible, pattern)
  assert.match(html, /Original publishers · no live feed/)
  assert.match(html, /No licensed headline snapshot is included/)
  assert.doesNotMatch(html, /market-news\.json|Latest headlines|Cedar Workshop/)
})

test('restored pricing and decision views keep visible scenario limitations and complete controls', async () => {
  const view = await compileView('../src/demo/MetaPricing.tsx')
  for (const component of [view.default, view.MetaDecision]) {
    const html = renderToStaticMarkup(React.createElement(component))
    const visible = html.replace(/<details\b[^>]*>[\s\S]*?<\/details>/gu, '')
    for (const pattern of [/Hypothetical calculations/, /not probabilities or a worst-case limit/, /entire value/, /Independently verify/, /dated/]) assert.match(visible, pattern)
  }
  const html = renderToStaticMarkup(React.createElement(view.default))
  for (const method of ['P/E', 'P/S', 'EV/EBITDA', 'P/book', 'P/NAV']) assert.ok(html.includes(method))
  assert.match(html, /Annual EPS growth assumption/)
  assert.match(html, /Target multiple assumption/)
  assert.match(html, /Company multiple history/)
  assert.match(html, /\$745\.52/)
  assert.match(html, /aria-live="polite"/)
  const scenarios = renderToStaticMarkup(React.createElement(view.MetaScenarioTable))
  assert.match(scenarios, /role="region" aria-label="META scenarios, scrollable table" tabindex="0"/)
})
