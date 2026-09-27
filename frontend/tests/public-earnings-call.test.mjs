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
    bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external',
    jsx: 'automatic', loader: { '.css': 'empty' }, logLevel: 'silent', metafile: true,
  })
  const module = { exports: {} }
  new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(require, module, module.exports)
  return { exports: module.exports, inputs: Object.keys(bundle.metafile.inputs) }
}

const [data, view, contexts, trends] = await Promise.all([
  compile('../src/demo/metaEarningsCall.ts'),
  compile('../src/demo/EarningsCallReview.tsx'),
  compile('../src/demo/metaThemeContext.ts'),
  compile('../src/demo/metaTrends.ts'),
])
const { META_CALL_TRANSCRIPT, earningsCall, callDiscussions, callRisks } = data.exports
const discussions = callDiscussions
const html = renderToStaticMarkup(React.createElement(view.exports.default))
const wordCount = value => value.trim().split(/\s+/u).length
const escapeHtml = value => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#x27;')
const discussionHtml = discussions.map(item => renderToStaticMarkup(React.createElement(view.exports.CallDiscussion, { item })))
const panels = markup => [...markup.matchAll(/<details\b([^>]*)>([\s\S]*?)<\/details>/gu)]

test('actual analyst exchanges retain independently verified issuer transcript speakers and page locators', () => {
  assert.equal(META_CALL_TRANSCRIPT, 'https://s21.q4cdn.com/399680738/files/doc_financials/2026/q2/META-Q2-2026-Earnings-Call-Transcript.pdf')
  assert.equal(earningsCall.source, META_CALL_TRANSCRIPT)
  assert.equal(earningsCall.sourceType, 'issuer_earnings_call_transcript')
  assert.equal(earningsCall.pageCount, 21)
  // Checked against the issuer PDF: question page, answer start, and excerpt page
  // are separate locators. An exchange can cross a page boundary.
  const expected = [
    ['ai-payoff', 'Brian Nowak · Morgan Stanley', 'Mark Zuckerberg · CEO', 10, 10, null],
    ['capex-uncertainty', 'Brian Nowak · Morgan Stanley', 'Susan Li · CFO', 10, 10, 10],
    ['enterprise-distribution', 'Eric Sheridan · Goldman Sachs', 'Mark Zuckerberg · CEO', 11, 11, null],
    ['funding', 'Eric Sheridan · Goldman Sachs', 'Susan Li · CFO', 11, 12, null],
    ['consumer-agents', 'Mark Shmulik · Bernstein', 'Mark Zuckerberg · CEO', 12, 13, 13],
    ['recommendations', 'Douglas Anmuth · JPMorgan', 'Susan Li · CFO', 14, 14, null],
    ['payback-delay', 'Douglas Anmuth · JPMorgan', 'Mark Zuckerberg · CEO', 14, 15, 16],
    ['lab-advantage', 'Justin Post · Bank of America', 'Mark Zuckerberg · CEO', 16, 16, null],
    ['model-scale', 'Ross Sandler · Barclays', 'Mark Zuckerberg · CEO', 17, 17, null],
    ['open-models', 'Ross Sandler · Barclays', 'Mark Zuckerberg · CEO', 17, 18, null],
    ['model-independence', 'Kenneth Gawrelski · Wells Fargo', 'Mark Zuckerberg · CEO', 18, 19, null],
    ['capacity-constraints', 'Kenneth Gawrelski · Wells Fargo', 'Susan Li · CFO', 18, 21, null],
  ]
  assert.deepEqual(discussions.map(item => [item.id, item.questionSpeaker, item.answerSpeaker, item.questionPage, item.answerPage, item.quote?.page ?? null]), expected)
  for (const item of discussions) {
    assert.equal(item.evidenceType, 'call_qa')
    assert.equal(item.questionSourceUrl, `${META_CALL_TRANSCRIPT}#page=${item.questionPage}`)
    assert.equal(item.sourceUrl, `${META_CALL_TRANSCRIPT}#page=${item.answerPage}`)
    if (item.quote) {
      assert.equal(item.quote.speaker, item.answerSpeaker)
      assert.equal(item.quote.sourceUrl, `${META_CALL_TRANSCRIPT}#page=${item.quote.page}`)
    }
  }
  assert.equal(data.inputs.length, 1, 'Public call analysis imports no private ledger or runtime artifacts')
})

test('July call evidence does not recast the September personal-agent launch as a reported-quarter event', () => {
  assert.equal(earningsCall.period, 'Q2 FY2026')
  assert.equal(earningsCall.date, '2026-07-29')
  assert.ok(earningsCall.date < '2026-09-08')
  assert.equal(earningsCall.analysisType, 'editorial_reading')
  assert.match(earningsCall.coverage, /paraphrases/)
  assert.match(callDiscussions.find(item => item.id === 'consumer-agents').answer, /still forthcoming/)
  assert.ok(discussions.every(item => item.sourceUrl.startsWith(`${META_CALL_TRANSCRIPT}#page=`)))
  assert.doesNotMatch(html, /introducing-muse-personal-ai-agent|September 8/)
})

test('brief excerpts and the complete editorial summary stay within the source reuse budget', () => {
  const excerpts = discussions.flatMap(item => item.quote ? [item.quote.text] : [])
  assert.deepEqual(excerpts, [
    'Infrastructure planning remains highly dynamic',
    'it needs to just work',
    'not getting value out of them until they’re online',
  ])
  assert.ok(excerpts.reduce((count, text) => count + wordCount(text), 0) <= 25)
  const prose = [earningsCall.summary, earningsCall.coverage, ...discussions.flatMap(item => [
    item.title, item.question, item.answer, item.stillUnclear, ...(item.quote ? [item.quote.text] : []),
  ])]
  assert.ok(prose.reduce((count, text) => count + wordCount(text), 0) <= 200)
})

test('every discussion keeps simple questions and answers visible with original wording closed', () => {
  for (const [index, item] of discussions.entries()) {
    const markup = discussionHtml[index]
    const disclosures = panels(markup)
    assert.equal(disclosures.length, 1)
    assert.doesNotMatch(disclosures[0][1], /\bopen(?:\s|=|$)/u)
    const visible = markup.replace(/<details\b[^>]*>[\s\S]*?<\/details>/gu, '')
    for (const text of [item.question, item.answer, item.stillUnclear]) assert.ok(visible.includes(escapeHtml(text)))
    assert.equal(visible.includes(escapeHtml(item.questionSpeaker)), false)
    if (item.quote) assert.equal(visible.includes(escapeHtml(item.quote.text)), false)
  }
})

test('eight themes preserve every question part and distinguish disclosures from Q&A', () => {
  assert.equal(discussions.length, 12)
  assert.equal(new Set(discussions.map(item => item.questionSpeaker)).size, 7)
  assert.equal(callRisks.length, 3)
  assert.ok(callRisks.every(item => discussions.includes(item) && item.caution))
  assert.equal(contexts.exports.metaThemeContexts.length, 8)
  const ids = new Set(contexts.exports.metaThemeContexts.map(item => item.id))
  assert.ok(discussions.every(item => ids.has(item.themeId)))
  for (const theme of contexts.exports.metaThemeContexts) {
    assert.ok(html.includes(escapeHtml(theme.title)))
    for (const id of theme.metricIds) assert.ok(trends.exports.metaTrendSeries.some(series => series.id === id))
  }
  for (const id of ['profitability', 'outlook']) assert.equal(discussions.some(item => item.themeId === id), false)
  for (const id of ['consumer', 'models']) {
    const theme = contexts.exports.metaThemeContexts.find(item => item.id === id)
    assert.deepEqual(theme.metricIds, [], 'No proxy chart pretends to measure unreported product economics')
    assert.match(theme.metricNote, /No /)
  }
  assert.match(html, /Caution and negative language/)
  assert.match(html, /Numbers to test this theme/)
  const sourcePanel = panels(html).at(-1)[2]
  assert.ok(sourcePanel.includes(`href="${META_CALL_TRANSCRIPT}"`))
  assert.ok(sourcePanel.includes(escapeHtml(earningsCall.coverage)))
  for (const item of discussions) assert.ok(sourcePanel.includes(`href="${item.questionSourceUrl}"`))
  for (const [attributes] of [...html.matchAll(/<details\b([^>]*)>/gu)].map(match => [match[1]])) assert.doesNotMatch(attributes, /\bopen(?:\s|=|$)/u)
})

test('expanded speaker context links to the quoted page when it differs from the answer start', () => {
  for (const [index, item] of discussions.entries()) {
    const context = panels(discussionHtml[index])[0][2]
    assert.ok(context.includes(escapeHtml(item.questionSpeaker)))
    assert.ok(context.includes(escapeHtml(item.answerSpeaker)))
    assert.ok(context.includes(`transcript page ${item.questionPage}`))
    assert.ok(context.includes(`transcript page ${item.answerPage}`))
    const target = item.quote?.sourceUrl ?? item.sourceUrl
    assert.ok(context.includes(`href="${target}"`), `${item.id} must open its relevant PDF page`)
    if (item.quote) {
      assert.ok(context.includes(escapeHtml(item.quote.text)))
      assert.ok(context.includes(`page ${item.quote.page}`))
    }
  }
})
