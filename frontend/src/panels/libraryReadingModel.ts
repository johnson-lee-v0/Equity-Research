// The reader preserves recorded evidence relationships; a bibliography alone
// never becomes a citation for a claim.
export type LibrarySource = {
  number: number
  id?: string
  url?: string
  title: string
  publisher?: string
  publishedAt?: string
  quote?: string
  locator?: string
}
export type LibrarySegment = { text: string; label?: string; citations: number[]; parts?: { text: string; citations: number[] }[] }
export type LibraryQuestion = {
  id: string
  kind?: 'question' | 'conclusion'
  question: string
  segments: LibrarySegment[]
  sources: LibrarySource[]
  status?: string
  asOf?: string
  origin?: string
}
type Row = Record<string, unknown>
export const record = (value: unknown): Row =>
  value && typeof value === 'object' && !Array.isArray(value) ? value as Row : {}
const text = (value: unknown) => typeof value === 'string' ? value.trim() : ''
const list = (value: unknown): unknown[] => Array.isArray(value) ? value : []

export function safeSourceUrl(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined
  try {
    const url = new URL(value)
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password
      ? url.href : undefined
  } catch { return undefined }
}

function sourceIdentity(row: Row): string {
  return text(row.id || row.source_id) || (safeSourceUrl(row.url)
    ? [safeSourceUrl(row.url), text(row.locator), text(row.quote)].join('|') : '') ||
    [text(row.title || row.label), text(row.quote), text(row.locator)].join('|')
}

export function normalizeQuestion(value: unknown, index = 0): LibraryQuestion {
  const row = typeof value === 'string' ? { question: value } : record(value)
  const sources: LibrarySource[] = []
  const identities = new Map<string, number>()
  const suppliedNumbers = new Map<number, number>()
  function addSource(value: unknown): number | undefined {
    const item = record(value)
    const identity = sourceIdentity(item)
    if (identity === '||') return undefined
    let number = identities.get(identity)
    if (number === undefined) {
      number = sources.length + 1
      identities.set(identity, number)
      sources.push({ number, id: text(item.id || item.source_id) || undefined,
        url: safeSourceUrl(item.url), title: text(item.title || item.label) || 'Retained source',
        publisher: text(item.publisher) || undefined,
        publishedAt: text(item.published_at || item.publishedAt || item.date) || undefined,
        quote: text(item.quote) || undefined, locator: text(item.locator) || undefined })
    }
    if (typeof item.number === 'number') suppliedNumbers.set(item.number, number)
    return number
  }
  const answerSources = [...new Set(list(row.sources).map(addSource).filter((n): n is number => n !== undefined))]
  const segments: LibrarySegment[] = []
  const supplied = list(row.answer_segments)
  if (supplied.length) {
    const inline = supplied.some((raw) => !text(record(raw).text) && list(record(raw).citation_numbers).length)
    const parts: { text: string; citations: number[] }[] = []
    for (const raw of supplied) {
      const segment = record(raw)
      const citations = [...new Set(list(segment.citation_numbers)
          .map((n) => typeof n === 'number' ? suppliedNumbers.get(n) : undefined)
          .filter((n): n is number => n !== undefined))]
      const content = typeof segment.text === 'string' ? segment.text : ''
      if (!content.trim() && !citations.length) continue
      if (inline) parts.push({ text: content, citations })
      else segments.push({ text: content, label: text(segment.label) || undefined, citations })
    }
    if (parts.length) segments.push({ text: parts.map((part) => part.text).join(''), citations: [], parts })
  } else if (text(row.answer)) {
    segments.push({ text: text(row.answer), citations: answerSources })
  }
  const management = record(row.managementAnswer)
  if (text(management.answer)) {
    const number = addSource(management.source)
    segments.push({ label: ['Management', text(management.speaker)].filter(Boolean).join(' · '),
      text: text(management.answer), citations: number === undefined ? [] : [number] })
    if (number !== undefined) {
      const source = sources[number - 1]
      source.locator ||= text(management.locator) || undefined
    }
  }
  for (const [key, label] of [['uncertainty', 'What remains uncertain'], ['decisionImpact', 'Why it matters'], ['decision_implication', 'Why it matters']] as const) {
    if (text(row[key])) segments.push({ text: text(row[key]), label, citations: [] })
  }
  for (const unknown of list(row.unknowns)) {
    if (text(unknown)) segments.push({ text: text(unknown), label: 'What remains uncertain', citations: [] })
  }
  for (const gap of list(row.citation_gaps)) {
    if (text(gap)) segments.push({ text: text(gap), label: 'Source availability', citations: [] })
  }
  return { id: text(row.id) || `question-${index + 1}`, question: text(row.question) || 'What does the research show?',
    kind: row.kind === 'conclusion' ? 'conclusion' : 'question',
    segments, sources, status: text(row.status) || undefined,
    asOf: text(row.as_of || row.asOf) || undefined, origin: text(row.origin) || undefined }
}

export function buildLibraryQuestions(packetValue: unknown, ideaValue: unknown, currentQuestions?: unknown): LibraryQuestion[] {
  const packet = record(packetValue)
  const idea = record(ideaValue)
  const questions: unknown[] = []
  const summary = text(packet.summary) || (!Object.keys(packet).length ? text(idea.legacySummary || idea.whyWaiting) : '')
  if (summary) questions.push({ id: 'conclusion', kind: 'conclusion', question: 'Saved research conclusion',
    answer_segments: [{ text: summary }, ...(text(packet.whyWaiting) && text(packet.whyWaiting) !== summary
      ? [{ text: text(packet.whyWaiting), label: 'Reasoning' }] : [])] })
  const supplied = list(currentQuestions)
  const saved = list(packet.questions)
  questions.push(...(supplied.length ? supplied : saved.length ? saved : list(idea.questions)))
  return questions.map(normalizeQuestion)
}

export function libraryReadingSections(rows: LibraryQuestion[]) {
  return {
    conclusions: rows.filter((row) => row.kind === 'conclusion'),
    questions: rows.filter((row) => row.kind !== 'conclusion'),
  }
}

// Supporting analysis is expressed as further questions, not a recursive dump
// of implementation fields. Its references stay attached to their original text.
export function buildSupportingQuestions(packetValue: unknown): LibraryQuestion[] {
  const packet = record(packetValue)
  const questions: unknown[] = []
  const valuation = record(packet.valuation)
  for (const [key, question] of [
    ['rationale', 'How was the valuation assessed?'], ['inputs', 'Which assumptions drive the valuation?'],
    ['scenarios', 'What are the valuation scenarios?'], ['stressLoss', 'What could the downside look like?'],
    ['positionGuidance', 'How does the research frame an allocation?'],
  ]) if (text(valuation[key])) questions.push({ question, answer: valuation[key], sources: valuation.sources })
  const call = record(packet.earningsCall)
  if (text(call.assessment)) questions.push({ question: 'What did the complete earnings call reveal?',
    answer: call.assessment, sources: call.source ? [call.source] : [],
    uncertainty: call.unavailableReason, decisionImpact: call.priorComparison })
  for (const [key, question] of [
    ['industryReadthrough', 'What does the wider industry evidence show?'],
    ['macroReadthrough', 'How do economic conditions affect the research?'],
  ]) {
    const section = record(packet[key])
    if (text(section.conclusion)) questions.push({ question, answer: section.conclusion, sources: section.sources })
    for (const item of list(section.observations)) {
      const observation = record(item)
      if (!text(observation.fact)) continue
      questions.push({ question: `What is the evidence on ${text(observation.dimension) || 'this development'}?`,
        answer: observation.fact, sources: observation.source ? [observation.source] : [],
        uncertainty: observation.alternativeExplanation,
        answer_segments: undefined, decisionImpact: [text(observation.inference), text(observation.decisionImpact)].filter(Boolean).join('\n\n') })
    }
  }
  for (const [key, question] of [
    ['thesisBreaks', 'What would invalidate the thesis?'], ['blockingUnknowns', 'Which unanswered questions prevent a decision?'],
    ['nonBlockingUnknowns', 'What else remains uncertain?'], ['nextReviewTrigger', 'When should this research be revisited?'],
    ['alternatives', 'Which alternatives were considered?'],
  ]) {
    const value = packet[key]
    const lines = Array.isArray(value) ? value.map(text).filter(Boolean) : [text(value)].filter(Boolean)
    if (lines.length) questions.push({ question, answer_segments: lines.map((line) => ({ text: line })) })
  }
  return questions.map((question, index) => normalizeQuestion({ ...record(question), id: `support-${index}` }, index))
}

export function bibliography(packetValue: unknown, ideaValue: unknown): LibrarySource[] {
  const packet = record(packetValue)
  const idea = record(ideaValue)
  return normalizeQuestion({ sources: list(packet.evidenceSources).length
    ? packet.evidenceSources : !Object.keys(packet).length ? idea.legacySources : [] }).sources
}
