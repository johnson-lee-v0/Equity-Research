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
    jsx: 'automatic', logLevel: 'silent', metafile: true,
  })
  const module = { exports: {} }
  new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(require, module, module.exports)
  return { exports: module.exports, inputs: Object.keys(bundle.metafile.inputs) }
}

const [data, view] = await Promise.all([
  compile('../src/demo/metaEarningsCall.ts'),
  compile('../src/demo/EarningsCallReview.tsx'),
])
const { META_CALL_TRANSCRIPT, earningsCall, callThemes, callRisks } = data.exports
const discussions = [...callThemes, ...callRisks]
const html = renderToStaticMarkup(React.createElement(view.exports.default))
const wordCount = value => value.trim().split(/\s+/u).length
const escapeHtml = value => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#x27;')
const disclosurePanels = [...html.matchAll(/<details\b([^>]*)>([\s\S]*?)<\/details>/gu)]

test('actual analyst exchanges retain independently verified issuer transcript speakers and page locators', () => {
  assert.equal(META_CALL_TRANSCRIPT, 'https://s21.q4cdn.com/399680738/files/doc_financials/2026/q2/META-Q2-2026-Earnings-Call-Transcript.pdf')
  assert.equal(earningsCall.source, META_CALL_TRANSCRIPT)
  assert.equal(earningsCall.sourceType, 'issuer_earnings_call_transcript')
  assert.equal(earningsCall.pageCount, 21)
  // Checked against the issuer PDF: question page, answer start, and excerpt page
  // are separate locators. An exchange can cross a page boundary.
  const expected = [
    ['ai-payoff', 'Brian Nowak · Morgan Stanley', 'Mark Zuckerberg · CEO', 10, 10, null],
    ['funding', 'Eric Sheridan · Goldman Sachs', 'Susan Li · CFO', 11, 12, null],
    ['consumer-agents', 'Mark Shmulik · Bernstein', 'Mark Zuckerberg · CEO', 12, 13, 13],
    ['capex-uncertainty', 'Brian Nowak · Morgan Stanley', 'Susan Li · CFO', 10, 10, 10],
    ['payback-delay', 'Douglas Anmuth · JPMorgan', 'Mark Zuckerberg · CEO', 14, 15, 16],
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
  assert.match(callThemes.find(item => item.id === 'consumer-agents').answer, /still forthcoming/)
  assert.ok(discussions.every(item => item.sourceUrl.startsWith(`${META_CALL_TRANSCRIPT}#page=`)))
  assert.doesNotMatch(html, /introducing-muse-personal-ai-agent|September 8/)
})

test('brief excerpts and the complete editorial summary stay within the source reuse budget', () => {
  const excerpts = discussions.flatMap(item => item.quote ? [item.quote.text] : [])
  assert.deepEqual(excerpts, [
    'it needs to just work',
    'Infrastructure planning remains highly dynamic',
    'not getting value out of them until they’re online',
  ])
  assert.ok(excerpts.reduce((count, text) => count + wordCount(text), 0) <= 25)
  const prose = [earningsCall.summary, earningsCall.coverage, ...discussions.flatMap(item => [
    item.title, item.question, item.answer, item.whyItMatters, ...(item.quote ? [item.quote.text] : []),
  ])]
  assert.ok(prose.reduce((count, text) => count + wordCount(text), 0) <= 200)
})

test('all original-wording and source panels are closed while simple questions and answers stay visible', () => {
  assert.equal(disclosurePanels.length, discussions.length + 1)
  for (const [, attributes] of disclosurePanels) assert.doesNotMatch(attributes, /\bopen(?:\s|=|$)/u)
  const visibleSummary = html.replace(/<details\b[^>]*>[\s\S]*?<\/details>/gu, '')
  assert.match(visibleSummary, /Themes/)
  assert.match(visibleSummary, /Caution and negative language/)
  for (const item of discussions) {
    assert.ok(visibleSummary.includes(escapeHtml(item.question)))
    assert.ok(visibleSummary.includes(escapeHtml(item.answer)))
    assert.ok(visibleSummary.includes(escapeHtml(item.whyItMatters)))
    assert.equal(visibleSummary.includes(escapeHtml(item.questionSpeaker)), false)
    if (item.quote) assert.equal(visibleSummary.includes(escapeHtml(item.quote.text)), false)
  }
  const fullSourcePanel = disclosurePanels.at(-1)[2]
  assert.ok(fullSourcePanel.includes(`href="${META_CALL_TRANSCRIPT}"`))
  assert.ok(fullSourcePanel.includes(escapeHtml(earningsCall.coverage)))
})

test('expanded speaker context links to the quoted page when it differs from the answer start', () => {
  for (const [index, item] of discussions.entries()) {
    const context = disclosurePanels[index][2]
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
