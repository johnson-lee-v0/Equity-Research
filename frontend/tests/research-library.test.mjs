import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'
const source = await readFile(new URL('../src/panels/libraryReadingModel.ts', import.meta.url), 'utf8')
const javascript = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const { normalizeQuestion, buildLibraryQuestions, buildSupportingQuestions, libraryReadingSections, bibliography, safeSourceUrl } = await import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`)

const filing = { label: 'Quarterly filing', url: 'https://example.com/filing' }
const call = { label: 'Call transcript', url: 'https://example.com/call' }

test('saved conclusion is separate from the five investment questions and their answers', () => {
  const packet = { summary: 'Watch for a lower price.', questions: Array.from({ length: 5 }, (_, index) => ({ question: `Question ${index + 1}`, answer: index === 4 ? '' : 'Supported answer.' })) }
  const sections = libraryReadingSections(buildLibraryQuestions(packet, {}))
  assert.equal(sections.conclusions.length, 1)
  assert.equal(sections.conclusions[0].segments[0].text, packet.summary)
  assert.equal(sections.questions.length, 5)
  assert.equal(sections.questions.filter((question) => question.segments.length).length, 4)
})

test('informational questions and summary-only research retain their own counts', () => {
  const informational = libraryReadingSections(buildLibraryQuestions({ questions: [{ id: 'conclusion', question: 'When is the next call?', answer: 'Date unavailable.' }] }, {}))
  assert.equal(informational.questions.length, 1)
  assert.equal(informational.conclusions.length, 0)
  const summaryOnly = libraryReadingSections(buildLibraryQuestions({ summary: 'Saved information.' }, {}))
  assert.equal(summaryOnly.questions.length, 0)
  assert.equal(summaryOnly.conclusions.length, 1)
})

test('legacy answers cite their attached sources and deduplicate repeated links', () => {
  const q = normalizeQuestion({ question: 'Is growth funded?', answer: 'Operating cash exceeds spending.', sources: [filing, filing, call] })
  assert.deepEqual(q.segments[0].citations, [1, 2])
  assert.equal(q.sources.length, 2)
  assert.equal(q.sources[0].url, filing.url)
})
test('management answers retain their separate source and location', () => {
  const q = normalizeQuestion({ question: 'How is growth funded?', answer: 'Research conclusion.', sources: [filing], managementAnswer: { answer: 'From our cash generation.', speaker: 'CFO', locator: 'Question from Analyst', source: call }, uncertainty: 'Future spending is not fixed.' })
  assert.deepEqual(q.segments.map((s) => s.citations), [[1], [2], []])
  assert.match(q.segments[1].label, /CFO/)
  assert.equal(q.sources[1].locator, 'Question from Analyst')
})
test('explicit segment mappings never attach every source or invent an unknown reference', () => {
  const q = normalizeQuestion({ sources: [{ ...filing, number: 8 }, { ...call, number: 12 }], answer_segments: [{ text: 'Fact from call.', citation_numbers: [12, 12, 999] }, { text: 'Analyst inference.', citation_numbers: [] }, { text: 'Filing fact.', citation_numbers: [8] }] })
  assert.deepEqual(q.segments.map((s) => s.citations), [[2], [], [1]])
})
test('bibliography does not become fabricated citations on summary or unanswered questions', () => {
  const packet = { summary: 'A saved conclusion.', questions: [{ question: 'What is next?', answer: 'It remains uncertain.' }], evidenceSources: [filing] }
  const q = buildLibraryQuestions(packet, {})
  assert.equal(q.length, 2)
  assert.deepEqual(q.map((r) => r.sources), [[], []])
  assert.equal(bibliography(packet, {}).length, 1)
})
test('a revision uses its own answers and sources rather than newer evidence', () => {
  const old = { questions: [{ question: 'What changed?', answer: 'Old conclusion.', sources: [filing] }] }
  assert.equal(buildLibraryQuestions(old, { packet: { questions: [] } })[0].segments[0].text, 'Old conclusion.')
  assert.equal(buildLibraryQuestions(old, {}, [{ question: 'Latest?', answer: 'New conclusion.', sources: [call] }])[0].sources[0].url, call.url)
})
test('unsafe protocols and embedded credentials never produce external links', () => {
  for (const url of ['javascript:alert(1)', 'data:text/html,hello', '//evil.test', 'https://user:secret@example.com', 'file:///tmp/x']) assert.equal(safeSourceUrl(url), undefined)
  const q = normalizeQuestion({ answer: 'A claim.', sources: [{ label: 'Untrusted URL', url: 'javascript:alert(1)' }] })
  assert.equal(q.sources[0].url, undefined)
})
test('pending and question-only company records remain readable without invented answers', () => {
  const q = buildLibraryQuestions(undefined, { questions: ['What drives returns?'] })[0]
  assert.equal(q.question, 'What drives returns?')
  assert.deepEqual(q.segments, [])
  assert.deepEqual(q.sources, [])
})
test('supporting analysis preserves observations, uncertainty and original source relationships', () => {
  const rows = buildSupportingQuestions({ valuation: { rationale: 'Scenario analysis.' }, industryReadthrough: { conclusion: 'Demand is growing.', sources: [call], observations: [{ dimension: 'customer spending', fact: 'Spending increased.', source: filing, inference: 'Adoption may grow.', alternativeExplanation: 'It could be timing.' }] }, nextReviewTrigger: 'Next results.' })
  assert.equal(rows.length, 4)
  assert.deepEqual(rows[0].sources, [])
  assert.equal(rows[1].sources[0].url, call.url)
  assert.equal(rows[2].sources[0].url, filing.url)
  assert.deepEqual(rows[2].segments.map((s) => s.citations), [[1], [], []])
})
test('inline citation-only API segments stay at their exact place inside the answer', () => {
  const q = normalizeQuestion({ sources: [{ ...filing, number: 4 }, { ...call, number: 7 }], answer_segments: [{ text: 'Cash grew ', citation_numbers: [] }, { text: '', citation_numbers: [4] }, { text: ' while management was cautious ', citation_numbers: [] }, { text: '', citation_numbers: [7] }, { text: '.', citation_numbers: [] }] })
  assert.equal(q.segments.length, 1)
  assert.deepEqual(q.segments[0].parts.map((p) => p.citations), [[], [1], [], [2], []])
  assert.equal(q.segments[0].parts[2].text, ' while management was cautious ')
})
test('same URL does not merge distinct archived snapshots or source locators', () => {
  const q = normalizeQuestion({ answer: 'Two periods compared.', sources: [{ ...filing, id: 'src_old' }, { ...filing, id: 'src_new' }, { ...filing, locator: 'Page 2' }, { ...filing, locator: 'Page 8' }] })
  assert.equal(q.sources.length, 4)
  assert.deepEqual(q.segments[0].citations, [1, 2, 3, 4])
})
test('selected revision normalized references override unbound raw packet text', () => {
  const raw = { questions: [{ question: 'Earlier question?', answer: 'Earlier claim [src_old].' }] }
  const normalized = [{ question: 'Earlier question?', sources: [{ ...filing, number: 1 }], answer_segments: [{ text: 'Earlier claim', citation_numbers: [] }, { text: '', citation_numbers: [1] }] }]
  const q = buildLibraryQuestions(raw, {}, normalized)[0]
  assert.equal(q.sources[0].url, filing.url)
  assert.deepEqual(q.segments[0].parts[1].citations, [1])
})
