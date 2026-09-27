import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/panels/research/transcriptHighlights.ts', import.meta.url), 'utf8')
const javascript = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const { managementHighlights, managementPreparedTurns } = await import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`)

function fixture() {
  const texts = [
    'Quarterly revenue grew 12% to $15 billion.',
    'However, currency changes added 2 percentage points to growth.',
    'We expect capital spending of $7 billion next year.',
    'We will now begin the question and answer session.',
    'Will sales growth slow down next quarter?',
    'We expect revenue growth to be 5% next quarter.',
  ]
  const turns = [
    { id: 'opening', role: 'management', speaker: 'Finance lead', section: 'Prepared remarks', statement_kind: 'management_remark', text: texts.slice(0, 3).join(' '), sentence_ids: [1, 2, 3] },
    { id: 'operator', role: 'operator', speaker: 'Operator', section: 'Prepared remarks', text: texts[3], sentence_ids: [4] },
    { id: 'question', role: 'analyst', speaker: 'Analyst', section: 'Q&A', text: texts[4], sentence_ids: [5] },
    { id: 'answer', role: 'management', speaker: 'Finance lead', section: 'Q&A', statement_kind: 'management_answer', text: texts[5], sentence_ids: [6] },
  ]
  const result = { source_hash: 'source-one', sentences: texts.map((text, i) => ({ id: i + 1, text })),
    reading_context: { full_text_available: true, turns, exchanges: [{ id: 'qa', question_turn_ids: ['question'], answer_turn_ids: ['answer'] }] } }
  const brief = { source_hash: 'source-one', status: 'completed', discussions: { opening: { group_id: 'opening', answer_status: 'not_a_question', bullets: [
    { kind: 'remark', summary: 'Revenue grew 12%; currency changes account for 2 percentage points.', turn_ids: ['opening'], quotes: [{ turn_id: 'opening', speaker: 'Wrong saved name', quote: texts.slice(0, 2).join(' '), offset: 0 }] },
  ] } } }
  return { result, brief }
}

test('opening highlights reuse the same-source brief and bind speaker and quote to prepared remarks', () => {
  const { result, brief } = fixture()
  const highlights = managementHighlights(result, brief)
  assert.equal(highlights.length, 1)
  assert.equal(highlights[0].text, brief.discussions.opening.bullets[0].summary)
  assert.equal(highlights[0].interpretation, true)
  assert.equal(highlights[0].quotes[0].speaker, 'Finance lead')
  assert.equal(highlights[0].quotes[0].offset, 0)
})

test('missing or stale summaries use exact source text and keep adjacent numerical qualifications', () => {
  const { result, brief } = fixture()
  brief.source_hash = 'a-different-call'
  const highlights = managementHighlights(result, brief)
  assert.ok(highlights.length >= 2)
  assert.ok(highlights.every((item) => item.interpretation === false))
  assert.match(highlights[0].text, /12%.*However, currency changes added 2 percentage points/)
  for (const item of highlights) {
    const quote = item.quotes[0]
    const turn = result.reading_context.turns.find((row) => row.id === quote.turn_id)
    assert.equal(turn.text.slice(quote.offset, quote.offset + quote.quote.length), quote.quote)
    assert.equal(item.text, quote.quote)
  }
})

test('analyst answers never become opening highlights even when an old parser labels them prepared remarks', () => {
  const { result } = fixture()
  result.reading_context.exchanges = []
  for (const turn of result.reading_context.turns.slice(2)) {
    turn.role = 'management'; turn.section = 'Prepared remarks'; turn.statement_kind = 'management_remark'
  }
  assert.deepEqual(managementPreparedTurns(result).map((row) => row.id), ['opening'])
  assert.ok(managementHighlights(result).every((item) => !item.text.includes('5% next quarter')))
})

test('a first analyst question forms a boundary even without an operator or Q&A heading', () => {
  const { result } = fixture()
  result.reading_context.turns.splice(1, 1)
  result.reading_context.exchanges = []
  result.reading_context.turns[1].section = 'Prepared remarks'
  result.reading_context.turns[2].section = 'Prepared remarks'
  assert.deepEqual(managementPreparedTurns(result).map((row) => row.id), ['opening'])
})

test('unknown source offsets, cross-discussion quotes, and mislabeled Q&A brief bullets are rejected', () => {
  for (const corrupt of [
    (b) => { b.quotes[0].offset = 1 },
    (b) => { b.quotes[0].turn_id = 'answer' },
    (b) => { b.turn_ids.push('answer') },
    (b) => { b.kind = 'answer' },
    (b) => { b.quotes[0].quote = 'Revenue grew 99%.' },
  ]) {
    const { result, brief } = fixture()
    corrupt(brief.discussions.opening.bullets[0])
    assert.ok(managementHighlights(result, brief).every((item) => item.interpretation === false))
  }
})

test('boilerplate warnings and records without full source context do not become business highlights', () => {
  const { result, brief } = fixture()
  brief.discussions.opening.bullets[0].summary = 'Plans are not promises: future revenue results may change.'
  assert.ok(managementHighlights(result, brief).every((item) => !item.text.startsWith('Plans are not promises')))
  result.reading_context.full_text_available = false
  assert.deepEqual(managementHighlights(result, brief), [])
})

test('highlight selection is bounded, stable, and does not mutate immutable saved results', () => {
  const { result, brief } = fixture()
  const before = JSON.stringify({ result, brief })
  const first = managementHighlights(result, undefined, 1)
  assert.equal(first.length, 1)
  assert.deepEqual(managementHighlights(result, undefined, 1), first)
  assert.equal(JSON.stringify({ result, brief }), before)
})

test('a selected adjusted result retains the preceding one-time benefit it refers to', () => {
  const { result, brief } = fixture()
  const quote = brief.discussions.opening.bullets[0].quotes[0]
  brief.discussions.opening.bullets = [
    { kind: 'remark', summary: 'A one-time benefit increased profit.', turn_ids: ['opening'], quotes: [quote] },
    { kind: 'remark', summary: 'Without that benefit, quarterly profit per share was $0.20.', turn_ids: ['opening'], quotes: [quote] },
  ]
  const pair = managementHighlights(result, brief, 2)
  assert.equal(pair.length, 2)
  assert.match(pair[0].text, /^A one-time benefit/)
  assert.match(pair[1].text, /^Without that benefit/)
  assert.ok(managementHighlights(result, brief, 1).every((row) => !row.text.startsWith('Without that')))
})

test('a dated greeting is not a business result', () => {
  const { result } = fixture()
  result.sentences.unshift({ id: 0, text: 'Good afternoon, and welcome to our fourth quarter 2026 earnings call.' })
  result.reading_context.turns[0].text = `${result.sentences[0].text} ${result.reading_context.turns[0].text}`
  result.reading_context.turns[0].sentence_ids.unshift(0)
  assert.ok(managementHighlights(result).every((row) => !row.text.startsWith('Good afternoon')))
})
