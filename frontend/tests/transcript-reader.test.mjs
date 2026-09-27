import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

// Exercise the same pure selectors used by the reader without adding a test runtime.
const source = await readFile(
  new URL('../src/panels/research/transcriptContext.ts', import.meta.url),
  'utf8',
)
const javascript = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText
const { buildReadingModel, discussionExcerpts, discussionMatchesOutsideBrief, groupEvidence, negativeSentences, orderDiscussionGroups, transcriptPageForSentence, transcriptPageForTurn } =
  await import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`)

function fixture() {
  const sentences = [
    {
      id: 1,
      text: 'Could weakening demand hurt your margins?',
      speaker: 'Analyst One',
      role: 'analyst',
      sentiment: { label: 'negative' },
    },
    {
      id: 2,
      text: 'Demand grew and our member renewal rate remains strong.',
      speaker: 'Finance Lead',
      role: 'management',
      sentiment: { label: 'positive' },
    },
    {
      id: 3,
      text: 'We have not seen a material headwind from this.',
      speaker: 'Finance Lead',
      role: 'management',
      sentiment: { label: 'negative' },
    },
    {
      id: 4,
      text: 'Our operations remain unchanged.',
      speaker: 'Chief Executive',
      role: 'management',
      sentiment: { label: 'neutral' },
    },
  ]
  return {
    sentences,
    reading_context: {
      full_text_available: true,
      transcript_text:
        'Analyst One: Could weakening demand hurt your margins?\nFinance Lead: Demand grew and our member renewal rate remains strong. We have not seen a material headwind from this.\nChief Executive: Our operations remain unchanged.\nOperator: Thank you.',
      turns: [
        {
          id: 'turn-1',
          speaker: 'Analyst One',
          role: 'analyst',
          section: 'Q&A',
          text: sentences[0].text,
          sentence_ids: [1],
        },
        {
          id: 'turn-2',
          speaker: 'Finance Lead',
          role: 'management',
          section: 'Q&A',
          text: `${sentences[1].text} ${sentences[2].text}`,
          sentence_ids: [2, 3],
        },
        {
          id: 'turn-3',
          speaker: 'Chief Executive',
          role: 'management',
          section: 'Q&A',
          text: sentences[3].text,
          sentence_ids: [4],
        },
        {
          id: 'turn-4',
          speaker: 'Operator',
          role: 'operator',
          section: 'Q&A',
          text: 'Thank you.',
          sentence_ids: [],
        },
      ],
      exchanges: [
        {
          id: 'exchange-1',
          question_turn_ids: ['turn-1'],
          answer_turn_ids: ['turn-2', 'turn-3'],
          context_turn_ids: ['turn-4'],
          sentence_ids: [1, 2, 3, 4],
          answered: true,
        },
      ],
    },
  }
}

test('a negative analyst question retains both positive and neutral management answers', () => {
  const result = fixture()
  const matches = negativeSentences(result, 'analyst')
  assert.deepEqual(
    matches.map((row) => row.id),
    [1],
  )
  const groups = groupEvidence(matches, buildReadingModel(result))
  assert.equal(groups.length, 1)
  assert.deepEqual(
    groups[0].turns.map((turn) => turn.id),
    ['turn-1', 'turn-2', 'turn-3', 'turn-4'],
  )
  assert.match(groups[0].turns[1].text, /renewal rate remains strong/)
  assert.match(groups[0].turns[2].text, /operations remain unchanged/)
})

test('management-only negative wording retains the analyst question and every answer', () => {
  const result = fixture()
  const matches = negativeSentences(result, 'management', 'material headwind')
  assert.deepEqual(
    matches.map((row) => row.id),
    [3],
  )
  const group = groupEvidence(matches, buildReadingModel(result))[0]
  assert.equal(group.turns[0].role, 'analyst')
  assert.equal(group.turns.filter((turn) => turn.role === 'management').length, 2)
})

test('multiple matching sentences produce one discussion without filtering its content', () => {
  const result = fixture()
  const groups = groupEvidence(
    [result.sentences[0], result.sentences[2], result.sentences[2]],
    buildReadingModel(result),
  )
  assert.equal(groups.length, 1)
  assert.deepEqual(
    groups[0].matches.map((row) => row.id),
    [1, 3],
  )
  assert.equal(groups[0].turns.length, 4)
})

test('source jumps support numeric and string IDs beyond the former 150-sentence cap', () => {
  const turns = Array.from({ length: 220 }, (_, index) => ({
    id: `turn-${index}`,
    speaker: 'Management',
    role: 'management',
    section: 'Q&A',
    text: `Remark ${index}`,
    sentence_ids: [index + 1],
  }))
  const model = buildReadingModel({
    sentences: [],
    reading_context: {
      turns,
      exchanges: [],
      transcript_text: 'Full source',
      full_text_available: true,
    },
  })
  assert.equal(transcriptPageForSentence(model, '220', 8).page, 27)
  assert.equal(transcriptPageForSentence(model, 220, 8).turn.id, 'turn-219')
  assert.equal(transcriptPageForSentence(model, 999, 8), null)
})

test('the full text export preserves source labels, line breaks and unanalyzed short utterances', () => {
  const result = fixture()
  const model = buildReadingModel(result)
  assert.equal(model.transcriptText, result.reading_context.transcript_text)
  assert.equal(model.fullTextAvailable, true)
  assert.match(model.transcriptText, /Operator: Thank you\.$/)
  assert.equal(model.turns.length, 4)
})

test('legacy reconstruction is explicitly incomplete and does not invent speaker roles', () => {
  const result = {
    sentences: [
      {
        id: 1,
        text: 'A retained passage.',
        speaker: 'Unknown Person',
        sentiment: { label: 'negative' },
      },
      {
        id: 2,
        text: 'A second retained passage.',
        speaker: 'Unknown Person',
        sentiment: { label: 'neutral' },
      },
    ],
  }
  const model = buildReadingModel(result)
  assert.equal(model.fullTextAvailable, false)
  assert.equal(model.turns[0].role, 'unknown')
  assert.equal(negativeSentences(result, 'management').length, 0)
  assert.equal(model.turns.length, 1)
  assert.match(model.transcriptText, /Unknown Person \(Role unverified\)/)
  assert.equal('turn_id' in result.sentences[0], false)
})

test('quoted TLDR pairs the analyst question with every management responder and qualifiers', () => {
  const result = fixture()
  const model = buildReadingModel(result)
  const group = groupEvidence(negativeSentences(result, 'analyst'), model)[0]
  const excerpts = discussionExcerpts(group, model)
  assert.deepEqual(excerpts.map((row) => row.label), ['Analyst asked', 'Management answered', 'Management answered'])
  assert.deepEqual(excerpts.map((row) => row.turn.speaker), ['Analyst One', 'Finance Lead', 'Chief Executive'])
  assert.match(excerpts[1].text, /renewal rate remains strong.*not seen a material headwind/)
  assert.match(excerpts[2].text, /remain unchanged/)
  for (const excerpt of excerpts) assert.ok(excerpt.turn.text.includes(excerpt.text), 'every summary is an exact source substring')
})

test('quoted TLDR retains preceding and following context and marks omitted surrounding text', () => {
  const texts = ['Thanks.', 'Our forecast is unchanged.', 'However, this depends on demand remaining stable.', 'The next update is in March.']
  const sentences = texts.map((text, index) => ({ id: index + 1, text, speaker: 'CFO', role: 'management', sentiment: { label: 'neutral' } }))
  const result = { sentences, reading_context: { turns: [{ id: 't', speaker: 'CFO', role: 'management', text: texts.join(' '), sentence_ids: [1, 2, 3, 4] }], exchanges: [], full_text_available: true } }
  const model = buildReadingModel(result)
  const excerpt = discussionExcerpts(groupEvidence([sentences[1]], model)[0], model)[0]
  assert.equal(excerpt.text, `${texts[0]} ${texts[1]} ${texts[2]}`)
  assert.equal(excerpt.sentenceId, 2)
  assert.equal(excerpt.omittedBefore, false)
  assert.equal(excerpt.omittedAfter, true)
  assert.equal(excerpt.turn.text, texts.join(' '))
})

test('brief prepared remarks do not acquire an invented analyst question or inferred answer', () => {
  const result = fixture()
  result.reading_context.exchanges = []
  const model = buildReadingModel(result)
  const excerpts = discussionExcerpts(groupEvidence([result.sentences[1]], model)[0], model)
  assert.equal(excerpts.length, 1)
  assert.equal(excerpts[0].label, 'Management said')
  assert.equal(excerpts[0].text, result.reading_context.turns[1].text)
})

test('short unanalysed replies retain their complete text and link by speaker turn', () => {
  const result = fixture()
  result.reading_context.turns[2].text = 'Yes.'
  result.reading_context.turns[2].sentence_ids = []
  const model = buildReadingModel(result)
  const excerpt = discussionExcerpts(groupEvidence([result.sentences[0]], model)[0], model)[2]
  assert.equal(excerpt.text, 'Yes.')
  assert.equal(excerpt.sentenceId, undefined)
  assert.equal(transcriptPageForTurn(model, excerpt.turn.id, 2).page, 1)
  assert.equal(transcriptPageForTurn(model, 'missing', 2), null)
})

test('Q&A-first ordering keeps all remarks and never reorders the underlying transcript', () => {
  const result = fixture()
  const intro = { id: 'intro', speaker: 'CFO', role: 'management', section: 'Prepared remarks', text: 'Forward-looking statements involve risk.', sentence_ids: [0] }
  result.reading_context.turns.unshift(intro)
  result.sentences.unshift({ id: 0, text: intro.text, speaker: 'CFO', role: 'management', sentiment: { label: 'negative' } })
  const model = buildReadingModel(result)
  const groups = groupEvidence(negativeSentences(result, 'all'), model)
  assert.deepEqual(orderDiscussionGroups(groups, model, true).map((row) => row.id), ['exchange-1', 'intro'])
  assert.deepEqual(orderDiscussionGroups(groups, model, false).map((row) => row.id), ['intro', 'exchange-1'])
  assert.equal(model.turns[0].id, 'intro')
  assert.equal(groups[0].id, 'intro')
})

test('the quoted question includes the actual ask even when the theme first matched its introduction', () => {
  const result = fixture()
  const intro = { id: 10, text: 'Sales growth has slowed.', speaker: 'Analyst One', role: 'analyst', sentiment: { label: 'negative' } }
  const context = { id: 11, text: 'Membership fees rose last year.', speaker: 'Analyst One', role: 'analyst', sentiment: { label: 'neutral' } }
  result.sentences.unshift(intro, context)
  const turn = result.reading_context.turns[0]
  turn.text = `${intro.text} ${context.text} ${turn.text}`
  turn.sentence_ids = [10, 11, 1]
  result.reading_context.exchanges[0].sentence_ids.unshift(10, 11)
  const model = buildReadingModel(result)
  const group = groupEvidence([intro], model)[0]
  const excerpts = discussionExcerpts(group, model)
  const question = excerpts[0]
  assert.equal(question.sentenceId, 10)
  assert.equal(question.text, turn.text)
  assert.match(question.text, /Membership fees rose last year\. Could weakening demand hurt your margins\?/)
  assert.equal(question.omittedBefore, false)
  assert.equal(question.highlight, intro.text)
  assert.deepEqual(discussionMatchesOutsideBrief(group, excerpts), [], 'the original match and full question remain visible together')
})

test('a multipart question keeps all asks when only the later management answer matched', () => {
  const result = fixture()
  result.sentences[0].text = 'How is ecommerce growing?'
  const renewal = { id: 5, text: 'Can you update renewal rates?', speaker: 'Analyst One', role: 'analyst', sentiment: { label: 'neutral' } }
  result.sentences.push(renewal)
  result.reading_context.turns[0].text = `${result.sentences[0].text} ${renewal.text}`
  result.reading_context.turns[0].sentence_ids.push(5)
  result.reading_context.exchanges[0].sentence_ids.push(5)
  const model = buildReadingModel(result)
  const group = groupEvidence([result.sentences[1]], model)[0]
  assert.equal(discussionExcerpts(group, model)[0].text, 'How is ecommerce growing? Can you update renewal rates?')
})

test('an analyst ask ending in a period stays paired with its answer before a later question mark', () => {
  const result = fixture()
  const sga = 'First, I was wondering if there is pressure within SG&A and any reason you would not be able to keep leveraging SG&A in the foreseeable future.'
  const followup = 'If I can sneak in a quick follow-up just separately on GLP-1s.'
  const glp = 'Could we see a larger headwind next year from the next round of GLP-1 pricing cuts?'
  result.sentences[0].text = sga
  result.sentences.push(
    { id: 5, text: followup, speaker: 'Analyst One', role: 'analyst', sentiment: { label: 'neutral' } },
    { id: 6, text: glp, speaker: 'Analyst One', role: 'analyst', sentiment: { label: 'negative' } },
  )
  result.reading_context.turns[0].text = `Thanks, guys. ${sga} ${followup} ${glp}`
  result.reading_context.turns[0].sentence_ids = [1, 5, 6]
  result.reading_context.exchanges[0].sentence_ids.push(5, 6)
  result.sentences[1].text = 'We are still seeing good leverage in SG&A.'
  result.reading_context.turns[1].text = result.sentences[1].text
  const model = buildReadingModel(result)
  const excerpts = discussionExcerpts(groupEvidence([result.sentences.at(-1)], model)[0], model)
  assert.equal(excerpts[0].text, result.reading_context.turns[0].text)
  assert.equal(excerpts[0].omittedBefore, false)
  assert.equal(excerpts[0].omittedAfter, false)
  assert.deepEqual(excerpts[0].includedSentenceIds, [1, 5, 6])
  assert.match(excerpts[1].text, /leverage in SG&A/)
})

test('management excerpts cannot turn a qualified contingency into an unconditional plan', () => {
  for (const condition of [
    'Only in a severe recession would those actions be necessary.',
    'If demand collapses, those actions would become necessary.',
    'Unless demand improves, those actions would be necessary.',
    'Assuming the recession deepens, those actions would be necessary.',
    'Provided demand weakens, those actions would be necessary.',
    'Subject to a major downturn, those actions would be necessary.',
    'Such cuts are not our base case.',
    'We do not expect to need those actions.',
    'We don’t plan to take those actions.',
    'We have no plans to take those actions.',
    'We are not seeing that downturn.',
    'We haven’t seen that downturn.',
  ]) {
    const texts = [condition, 'These are the actions in that scenario.', 'We would cut capital spending.', 'We would delay warehouse openings.']
    const sentences = texts.map((text, index) => ({ id: index + 1, text, speaker: 'CFO', role: 'management', sentiment: { label: 'neutral' } }))
    const result = { sentences, reading_context: { turns: [{ id: 't', speaker: 'CFO', role: 'management', text: texts.join(' '), sentence_ids: [1, 2, 3, 4] }], exchanges: [], full_text_available: true } }
    const model = buildReadingModel(result)
    const excerpt = discussionExcerpts(groupEvidence([sentences[2]], model)[0], model)[0]
    assert.equal(excerpt.text, texts.join(' '), 'a qualification outside the brief forces retention of the whole speaker turn')
    assert.equal(excerpt.omittedBefore, false)
    assert.equal(excerpt.omittedAfter, false)
  }
})

test('ordinary modal words in another topic do not expand an answer into the whole turn', () => {
  const texts = [
    'Tariffs would have an impact on imported goods.',
    'We could adjust the assortment if suppliers change prices.',
    'Renewals remain strong.',
    'The renewal rate was 92.7%.',
    'This reflects the membership mix.',
    'Our online assortment might expand next quarter.',
    'This is only part of our merchandising work.',
  ]
  const sentences = texts.map((text, index) => ({ id: index + 1, text, speaker: 'CFO', role: 'management', sentiment: { label: 'neutral' } }))
  const result = { sentences, reading_context: { turns: [{ id: 't', speaker: 'CFO', role: 'management', text: texts.join(' '), sentence_ids: sentences.map((sentence) => sentence.id) }], exchanges: [], full_text_available: true } }
  const model = buildReadingModel(result)
  const excerpt = discussionExcerpts(groupEvidence([sentences[3]], model)[0], model)[0]
  assert.equal(excerpt.text, texts.slice(2, 5).join(' '))
  assert.equal(excerpt.fullContextRetained, false)
  assert.equal(excerpt.omittedBefore, true)
  assert.equal(excerpt.omittedAfter, true)
})

test('repeated sentences retain their distinct occurrence for excerpts and transcript highlights', () => {
  const texts = ['Growth improved.', 'Our European stores led the improvement.', 'Growth improved.', 'North American stores followed later.']
  const sentences = texts.map((text, index) => ({ id: index + 1, text, speaker: 'CFO', role: 'management', sentiment: { label: 'positive' } }))
  const result = { sentences, reading_context: { turns: [{ id: 't', speaker: 'CFO', role: 'management', text: texts.join(' '), sentence_ids: [1, 2, 3, 4] }], exchanges: [], full_text_available: true } }
  const model = buildReadingModel(result)
  const group = groupEvidence([sentences[2]], model)[0]
  const excerpt = discussionExcerpts(group, model)[0]
  assert.equal(excerpt.text, texts.slice(1).join(' '))
  assert.equal(excerpt.highlightStart, texts[1].length + 1)
  assert.equal(model.sentenceOffsets.get('3').start, `${texts[0]} ${texts[1]} `.length)
  assert.equal(model.sentenceOffsets.get('1').start, 0)
  assert.deepEqual(excerpt.includedSentenceIds, [2, 3, 4])
  assert.deepEqual(discussionMatchesOutsideBrief({ ...group, matches: [sentences[0]] }, [excerpt]).map((row) => row.id), [1])
})
