import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const require = createRequire(import.meta.url)
const { build } = createRequire(require.resolve('vite'))('esbuild')
const bundle = await build({
  stdin: {
    contents: "export { default as EarningsBrief } from './EarningsBrief'; export { default as Explorer } from './EarningsTrendExplorer'; export { TranscriptView, ThemeTrendContext } from './TranscriptReader'; export * from './earningsReviewModel'; export { buildReadingModel } from './transcriptContext'",
    resolveDir: fileURLToPath(new URL('../src/panels/research', import.meta.url)),
  },
  bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external', jsx: 'automatic',
  loader: { '.css': 'empty' }, logLevel: 'silent',
})
const compiled = { exports: {} }
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(require, compiled, compiled.exports)
const { EarningsBrief, Explorer, TranscriptView, ThemeTrendContext, groupedEarningsTrends, relatedThemeTrends, transcriptThemeOverview, buildReadingModel, discussionAnswerCoverage } = compiled.exports
const render = (component, props) => renderToStaticMarkup(React.createElement(component, props))
const series = (id, label, areas, value = 12) => ({ id, label, area_ids: areas, unit: 'percent', frequency: 'quarterly', basis: 'Reported quarterly test measure', points: [{ period: 'Q1 FY2026', kind: 'actual', value, url: `https://investor.example.com/${id}` }] })
const trends = (items) => ({ version: 'test', status: 'complete', series: items, gaps: [] })

function transcript() {
  const themeNames = ['Demand and growth', 'Margins and costs', 'Guidance and outlook', 'Capital and liquidity', 'Risk and regulation', 'Products and innovation']
  const sentences = []
  const turns = []
  const exchanges = []
  const discussions = {}
  for (let i = 0; i < 6; i++) {
    const question = `Why did measure ${i + 1} change?`
    const answer = `Management explained measure ${i + 1}, but the next quarter remains uncertain.`
    sentences.push({ id: i * 2, text: question, role: 'analyst', speaker: `Analyst ${i}`, section: 'Q&A', sentiment: { label: 'neutral', cues: [] } }, { id: i * 2 + 1, text: answer, role: 'management', speaker: 'Finance lead', section: 'Q&A', sentiment: { label: 'negative', cues: [{ term: 'uncertain' }] } })
    turns.push({ id: `q${i}`, text: question, speaker: `Analyst ${i}`, role: 'analyst', section: 'Q&A', sentence_ids: [i * 2] }, { id: `a${i}`, text: answer, speaker: 'Finance lead', role: 'management', section: 'Q&A', sentence_ids: [i * 2 + 1] })
    exchanges.push({ id: `exchange${i}`, question_turn_ids: [`q${i}`], answer_turn_ids: [`a${i}`], context_turn_ids: [], sentence_ids: [i * 2, i * 2 + 1], answered: true })
    discussions[`exchange${i}`] = { group_id: `exchange${i}`, answer_status: i === 0 ? 'partial' : 'answered', bullets: [
      { kind: 'question', summary: `What changed in topic ${i + 1}?`, turn_ids: [`q${i}`], quotes: [{ turn_id: `q${i}`, speaker: `Analyst ${i}`, quote: question, offset: 0 }] },
      { kind: 'answer', summary: `The present result was explained; the future is not settled for topic ${i + 1}.`, turn_ids: [`a${i}`], quotes: [{ turn_id: `a${i}`, speaker: 'Finance lead', quote: answer, offset: 0 }] },
    ] }
  }
  return { method: 'test', summary: 'Saved call', limitations: [], source_hash: 'call-a', sentiment: {}, speakers: [], entities: [], sentences,
    themes: themeNames.map((name, i) => ({ name, mentions: 2, evidence: [{ sentence_id: i * 2 }, { sentence_id: i * 2 + 1 }] })),
    reading_context: { full_text_available: true, transcript_text: turns.map(turn => `${turn.speaker}: ${turn.text}`).join('\n'), turns, exchanges },
    plain_language: { status: 'completed', source_hash: 'call-a', gaps: [], discussions },
  }
}

test('every saved metric survives category grouping, including EPS, cash and unfamiliar measures', () => {
  const saved = trends([
    series('quarterly_revenue', 'Revenue', ['demand']), series('revenue_yoy_growth', 'Revenue growth', ['growth']),
    series('operating_margin', 'Operating margin', ['margins']), series('diluted_eps', 'Diluted EPS', ['margins']),
    series('operating_cash_flow', 'Operating cash flow', ['capital']), series('free_cash_flow', 'Free cash flow', ['cash']),
    series('capex_quarterly', 'Quarterly capital expenditure', ['capital']), series('capex_cash_ppe', 'Cash purchases of PP&E', ['capital']),
    series('new_measure', 'New saved measure', ['new_area']),
  ])
  const categories = Object.fromEntries(groupedEarningsTrends(saved).map(item => [item.id, item.series.map(row => row.id)]))
  assert.deepEqual(categories.growth, ['quarterly_revenue', 'revenue_yoy_growth'])
  assert.deepEqual(categories.margins, ['operating_margin'])
  assert.deepEqual(categories.cash, ['diluted_eps', 'operating_cash_flow', 'free_cash_flow'])
  assert.deepEqual(categories.capital, ['capex_quarterly', 'capex_cash_ppe'])
  assert.deepEqual(categories.other, ['new_measure'])
  assert.equal(Object.values(categories).flat().length, saved.series.length)
})

test('the shared explorer displays supplied cash figures and retains empty-category and coverage information', () => {
  const saved = trends([series('diluted_eps', 'Diluted EPS', ['earnings'], -0.4)])
  saved.gaps = ['Q2 has not been reported.']
  const html = render(Explorer, { result: saved })
  for (const label of ['Growth', 'Margins', 'Earnings &amp; cash', 'CapEx &amp; guidance']) assert.ok(html.includes(label))
  assert.match(html, /Diluted EPS over time/)
  assert.match(html, /Q2 has not been reported/)
  assert.match(html, /Values &amp; sources/)
  assert.match(render(Explorer, {}), /Historical figures have not been collected/)
  assert.doesNotMatch(render(Explorer, {}), /<svg/)
})

test('guidance comparisons remain reachable even when there are no historical series', () => {
  const html = render(Explorer, { result: { ...trends([]), capex_guidance: { comparisons: [{ period: 'FY2025', initial_low: 3, initial_high: 4, actual: 4.2 }] } } })
  assert.match(html, /Capex guidance track record/)
  assert.match(html, /Compare guidance with actual spending/)
  assert.match(html, /No capital-spending history was collected/)
})

test('the theme overview preserves all themes and groups rather than limiting analysis to a preview', () => {
  const result = transcript()
  result.themes[0].evidence.push(...result.themes.slice(1, 4).flatMap(theme => theme.evidence))
  const overview = transcriptThemeOverview(result, buildReadingModel(result), result.plain_language)
  assert.equal(overview.length, 6)
  assert.equal(overview[0].groups.length, 4)
  const html = render(TranscriptView, { result })
  assert.match(html, /All 6 business themes/)
  assert.match(html, /Explore 4 discussions/)
  assert.match(html, /1–3 of 4 discussions/)
  assert.match(html, /Products and innovation/)
  assert.match(html, /Negative language/)
  assert.match(html, /Full transcript/)
  assert.match(html, /Still unclear/)
  assert.match(html, /Some parts of the question were left unanswered/)
  assert.match(html, /<strong>Asked: <\/strong>/)
  assert.match(html, /<strong>Answered: <\/strong>/)
  assert.doesNotMatch(html, /<details[^>]*\bopen(?:[\s=>])/)
})

test('a later source-matched discussion represents the theme without reordering or removing the full discussions', () => {
  const result = transcript()
  result.themes[0].evidence = [{ sentence_id: 0 }, { sentence_id: 2 }, { sentence_id: 3 }]
  const original = JSON.stringify(result)
  const overview = transcriptThemeOverview(result, buildReadingModel(result), result.plain_language)[0]
  assert.equal(overview.group.id, 'exchange1')
  assert.deepEqual(overview.groups.map(group => group.id), ['exchange0', 'exchange1'])
  assert.deepEqual(overview.previewBullets.map(bullet => bullet.summary), result.plain_language.discussions.exchange1.bullets.map(bullet => bullet.summary))
  assert.equal(JSON.stringify(result), original)
})

function multipartTranscript() {
  const questionA = 'How will you reach business buyers?'
  const questionB = 'How will construction be financed?'
  const answerA = 'We can build on our existing business relationships.'
  const answerB = 'Cash from operations, borrowing and partners will fund construction.'
  const turns = [
    { id: 'asks', text: `${questionA} ${questionB}`, speaker: 'Analyst', role: 'analyst', section: 'Q&A', sentence_ids: [1, 2] },
    { id: 'sales', text: answerA, speaker: 'CEO', role: 'management', section: 'Q&A', sentence_ids: [3] },
    { id: 'funding', text: answerB, speaker: 'CFO', role: 'management', section: 'Q&A', sentence_ids: [4] },
  ]
  const bullet = (kind, summary, turn_id, speaker, quote, offset = 0) => ({ kind, summary, turn_ids: [turn_id], quotes: [{ turn_id, speaker, quote, offset }] })
  return { ...transcript(),
    sentences: [questionA, questionB, answerA, answerB].map((text, index) => ({ id: index + 1, text, role: index < 2 ? 'analyst' : 'management', speaker: index < 2 ? 'Analyst' : index === 2 ? 'CEO' : 'CFO', section: 'Q&A', sentiment: { label: 'neutral', cues: [] } })),
    themes: [
      { name: 'Capital and liquidity', mentions: 2, evidence: [{ sentence_id: 2 }, { sentence_id: 4 }] },
      { name: 'Demand and growth', mentions: 2, evidence: [{ sentence_id: 1 }, { sentence_id: 3 }] },
    ],
    reading_context: { full_text_available: true, transcript_text: turns.map(turn => turn.text).join('\n'), turns, exchanges: [{ id: 'multipart', question_turn_ids: ['asks'], answer_turn_ids: ['sales', 'funding'], context_turn_ids: [], sentence_ids: [1, 2, 3, 4], answered: true }] },
    plain_language: { source_hash: 'call-a', status: 'completed', gaps: [], discussions: { multipart: { group_id: 'multipart', answer_status: 'answered', bullets: [
      bullet('question', 'How will business customers be reached?', 'asks', 'Analyst', questionA),
      bullet('question', 'Where will the construction money come from?', 'asks', 'Analyst', questionB, questionA.length + 1),
      bullet('answer', 'Existing business relationships help distribution.', 'sales', 'CEO', answerA),
      bullet('answer', 'Operating cash, borrowing and partners fund construction.', 'funding', 'CFO', answerB),
    ] } } },
  }
}

test('a multipart funding preview selects the financing question and CFO answer instead of enterprise sales', () => {
  const result = multipartTranscript()
  const overview = transcriptThemeOverview(result, buildReadingModel(result), result.plain_language)
  assert.deepEqual(overview[0].previewBullets.map(bullet => bullet.summary), [
    'Where will the construction money come from?', 'Operating cash, borrowing and partners fund construction.',
  ])
  assert.deepEqual(overview[1].previewBullets.map(bullet => bullet.summary), [
    'How will business customers be reached?', 'Existing business relationships help distribution.',
  ])
  const html = render(TranscriptView, { result })
  const card = html.match(/<article class="transcript-overview-card">[\s\S]*?<\/article>/)[0]
  assert.match(card, /Where will the construction money come from/)
  assert.match(card, /Operating cash, borrowing and partners/)
  assert.doesNotMatch(card, /Existing business relationships help distribution/)
  assert.match(html, /Existing business relationships help distribution/, 'The full multipart discussion still includes its other answer.')
})

test('when only one side of a multipart discussion matches, the preview retains the full pair instead of guessing', () => {
  const result = multipartTranscript()
  result.themes[0].evidence = [{ sentence_id: 4 }]
  const preview = transcriptThemeOverview(result, buildReadingModel(result), result.plain_language)[0]
  assert.deepEqual(preview.previewBullets, result.plain_language.discussions.multipart.bullets)
})

test('a prepared-theme preview requires matching source wording and never falls back to unrelated remarks', () => {
  const sales = 'Revenue grew as more customers purchased our products.'
  const risk = 'A legal challenge could delay the launch of our new service.'
  const remarks = [{ kind: 'remark', summary: 'Sales increased.', turn_ids: ['opening'], quotes: [{ turn_id: 'opening', speaker: 'CEO', quote: sales, offset: 0 }] }]
  const result = { ...transcript(),
    sentences: [sales, risk].map((text, index) => ({ id: index, text, role: 'management', speaker: 'CEO', section: 'Prepared remarks', sentiment: { label: 'neutral', cues: [] } })),
    themes: [{ name: 'Risk and regulation', mentions: 1, evidence: [{ sentence_id: 1 }] }],
    reading_context: { full_text_available: true, transcript_text: `${sales} ${risk}`, turns: [{ id: 'opening', text: `${sales} ${risk}`, speaker: 'CEO', role: 'management', section: 'Prepared remarks', sentence_ids: [0, 1] }], exchanges: [] },
    plain_language: { source_hash: 'call-a', status: 'partial', gaps: [], discussions: { opening: { group_id: 'opening', answer_status: 'not_a_question', bullets: remarks } } },
  }
  let preview = transcriptThemeOverview(result, buildReadingModel(result), result.plain_language)[0]
  assert.equal(preview.brief, undefined)
  assert.deepEqual(preview.previewBullets, [])
  assert.equal(preview.groups.length, 1)
  const card = render(TranscriptView, { result }).match(/<article class="transcript-overview-card">[\s\S]*?<\/article>/)[0]
  assert.match(card, /A plain-language preview is not saved/)
  assert.match(card, /Explore 1 discussion/)
  assert.doesNotMatch(card, /Sales increased/)

  remarks.push({ kind: 'remark', summary: 'Legal action could postpone the new service.', turn_ids: ['opening'], quotes: [{ turn_id: 'opening', speaker: 'CEO', quote: risk, offset: sales.length + 1 }] })
  preview = transcriptThemeOverview(result, buildReadingModel(result), result.plain_language)[0]
  assert.deepEqual(preview.previewBullets.map(bullet => bullet.summary), ['Legal action could postpone the new service.'])
})

test('a stale explanation never becomes the current call overview or an answer-completeness claim', () => {
  const result = transcript()
  result.plain_language.source_hash = 'other-call'
  const html = render(TranscriptView, { result })
  assert.doesNotMatch(html, /What changed in topic|The present result was explained|Some parts of the question were left unanswered/)
  assert.match(html, /A plain-language preview is not saved/)
  assert.match(html, /Not assessed in the saved explanations/)
  assert.match(html, /Management explained measure 1/)
  assert.doesNotMatch(html, /<details[^>]*\bopen(?:[\s=>])/)
})

test('answer coverage distinguishes an absent answer from an answer whose meaning was not assessed', () => {
  const group = { exchange: { answered: true } }
  assert.equal(discussionAnswerCoverage(group).text, 'Not assessed in the saved explanations.')
  assert.equal(discussionAnswerCoverage(group, 'unclear').label, 'Still unclear')
  assert.match(discussionAnswerCoverage({ exchange: { answered: false } }).text, /No identifiable management answer/)
  assert.match(discussionAnswerCoverage({}).text, /No complete question-and-answer link/)
})

test('related figures use the saved topic mapping, preserve zero and never turn a forecast into an actual', () => {
  const saved = trends([series('sales', 'Sales growth', ['demand'], 0), series('margin', 'Operating margin', ['margins'], 20)])
  assert.deepEqual(relatedThemeTrends(saved, ['Demand and growth']).map(row => row.id), ['sales'])
  assert.deepEqual(relatedThemeTrends(saved, ['Unmapped theme']), [])
  const html = render(ThemeTrendContext, { series: relatedThemeTrends(saved, ['Demand and growth']) })
  assert.match(html, /0% · Q1 FY2026 · reported actual/)
  assert.match(html, /https:\/\/investor.example.com\/sales/)
  assert.doesNotMatch(html, /Operating margin/)
  saved.series[0].points[0].kind = 'projection'
  assert.match(render(ThemeTrendContext, { series: [saved.series[0]] }), /No reported actual was saved/)
})

test('management commentary hides original words by default without losing the source or risk/outlook topics', () => {
  const result = transcript()
  result.sentences.push({ id: 99, role: 'management', speaker: 'Finance lead', text: 'Our operating margin declined as expenses rose, and adjusted margin excludes a temporary charge.' })
  const html = render(EarningsBrief, { result, ticker: 'TEST', onRead: () => {}, trends: trends([]) })
  assert.match(html, /Risks to revisit/)
  assert.match(html, /Outlook/)
  assert.match(html, /<details class="earnings-commentary-source"><summary>Management’s original words and source/)
  assert.match(html, /Our operating margin declined/)
  assert.doesNotMatch(html, /<details[^>]*\bopen(?:[\s=>])/)
})
