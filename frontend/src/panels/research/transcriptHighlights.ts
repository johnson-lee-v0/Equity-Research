import type { PlainLanguageBriefs, TranscriptResult, TranscriptTurn } from './DocumentViews'

export type HighlightQuote = { turn_id: string; speaker: string; quote: string; offset: number }
export type ManagementHighlight = {
  id: string
  text: string
  interpretation: boolean
  quotes: HighlightQuote[]
}

const BUSINESS = /\b(revenue|sales|growth|grew|profit|earnings|margin|cash|capital|capex|spending|members?|renewal|stores?|warehouses?|users?|advertis\w*|demand|inventory|inventories|costs?|outlook|guidance|invest\w*|dividend|repurchas\w*|expenses?|benefit|recovery)\b/i
const DISCLAIMER = /forward[- ]looking|safe harbor|litigation reform|SEC filings|actual results (?:may|could)|risks and uncertainties|plans are not promises|opening warning|adjusted accounting|non-GAAP (?:financial )?measures/i
const GREETING = /^(?:good (?:morning|afternoon|evening)|thank you|thanks(?:\s|,)|welcome|hello everyone)/i
const DEPENDS_ON_PRIOR = /^(?:(?:without|excluding|including|because of|despite)\s+(?:that|this|these|those)|(?:this|that)\s+(?:means|reflects|includes|excludes|compares)|however\b|but\b)/i
const QA_START = /\b(?:begin|start|open|move|turn|take|go|ready)\b.{0,50}\b(?:question(?:s)?(?:\s*[-–]?\s*(?:and|&)\s*[-–]?\s*answers?)?|Q\s*&\s*A)\b/i

function score(text: string) {
  return (/[\d$%]/.test(text) ? 4 : 0)
    + (/\b(quarter|year|fiscal|Q[1-4]|FY\s*\d)\b/i.test(text) ? 2 : 0)
    + (/\b(revenue|sales|earnings|profit|margin|cash|capital|capex|renewal|users|guidance|benefit)\b/i.test(text) ? 2 : 0)
}

/** Only pre-Q&A management turns qualify, even if an older parser mislabeled later answers. */
export function managementPreparedTurns(result: TranscriptResult): TranscriptTurn[] {
  const context = result.reading_context
  if (!context?.full_text_available) return []
  const questions = new Set((context.exchanges || []).flatMap((exchange) => exchange.question_turn_ids))
  const boundary = context.turns.findIndex((turn) => turn.role === 'analyst'
    || questions.has(turn.id) || turn.statement_kind === 'analyst_question'
    || /^Q\s*&\s*A|question(?:s)?\s*(?:and|&)\s*answer/i.test(turn.section || '')
    || (turn.role === 'operator' && QA_START.test(turn.text)))
  return context.turns.slice(0, boundary < 0 ? context.turns.length : boundary).filter((turn) =>
    turn.role === 'management' && !turn.exchange_id
    && !['management_answer', 'analyst_question'].includes(turn.statement_kind || '')
    && /prepared|presentation|opening/i.test(turn.section || ''))
}

/** Reuse source-bound reading aids; missing coverage uses exact saved words, never invented facts. */
export function managementHighlights(result: TranscriptResult, briefs?: PlainLanguageBriefs, limit = 8): ManagementHighlight[] {
  const turns = managementPreparedTurns(result)
  const byId = new Map(turns.map((turn) => [turn.id, turn]))
  const candidates: (ManagementHighlight & { rank: number; position: number; needs?: string })[] = []
  const covered = new Set<string>()
  if (result.source_hash && briefs?.source_hash === result.source_hash) {
    for (const discussion of Object.values(briefs.discussions || {})) {
      if (discussion.answer_status !== 'not_a_question') continue
      for (const [index, bullet] of discussion.bullets.entries()) {
        if (bullet.kind !== 'remark' || !BUSINESS.test(bullet.summary) || DISCLAIMER.test(bullet.summary) || GREETING.test(bullet.summary)
          || !bullet.turn_ids.length || bullet.turn_ids.some((id) => !byId.has(id))
          || !bullet.quotes.length || bullet.turn_ids.some((id) => !bullet.quotes.some((quote) => quote.turn_id === id))) continue
        const quotes: HighlightQuote[] = []
        for (const quote of bullet.quotes) {
          const turn = byId.get(quote.turn_id)
          if (!turn || !bullet.turn_ids.includes(turn.id) || !quote.quote.trim()
            || !Number.isInteger(quote.offset) || quote.offset < 0
            || turn.text.slice(quote.offset, quote.offset + quote.quote.length) !== quote.quote) break
          quotes.push({ ...quote, speaker: turn.speaker })
        }
        if (quotes.length !== bullet.quotes.length) continue
        for (const id of bullet.turn_ids) covered.add(id)
        candidates.push({ id: `${discussion.group_id}-${index}`, text: bullet.summary, interpretation: true, quotes,
          rank: score(bullet.summary), position: Math.min(...bullet.turn_ids.map((id) => turns.findIndex((turn) => turn.id === id))) * 1000 + index,
          needs: DEPENDS_ON_PRIOR.test(bullet.summary) ? `${discussion.group_id}-${index - 1}` : undefined })
      }
    }
  }
  const sentences = new Map(result.sentences.map((sentence) => [String(sentence.id), sentence]))
  for (const [position, turn] of turns.entries()) {
    if (covered.has(turn.id)) continue
    let cursor = 0
    for (const [index, id] of turn.sentence_ids.entries()) {
      const sentence = sentences.get(String(id))
      if (!sentence) continue
      const offset = turn.text.indexOf(sentence.text, cursor)
      if (offset < 0) continue
      cursor = offset + sentence.text.length
      if (sentence.text.length < 35 || sentence.text.length > 700 || !BUSINESS.test(sentence.text)
        || DISCLAIMER.test(sentence.text) || GREETING.test(sentence.text) || DEPENDS_ON_PRIOR.test(sentence.text) || score(sentence.text) < 4) continue
      // Keep adjacent qualifications with the highlighted statement where available.
      const following = sentences.get(String(turn.sentence_ids[index + 1]))
      const text = following && /^(?:but|however|excluding|including|this (?:excludes|includes)|subject to|unless|if|assuming)\b/i.test(following.text)
        && turn.text.slice(cursor).trimStart().startsWith(following.text)
        ? turn.text.slice(offset, turn.text.indexOf(following.text, cursor) + following.text.length)
        : sentence.text
      candidates.push({ id: `source-${turn.id}-${id}`, text, interpretation: false,
        quotes: [{ turn_id: turn.id, speaker: turn.speaker, quote: text, offset }],
        rank: score(text), position: position * 1000 + index })
    }
  }
  const seen = new Set<string>()
  const unique = candidates.sort((a, b) => b.rank - a.rank || a.position - b.position).filter((item) => {
    const key = item.text.toLocaleLowerCase().replace(/\s+/g, ' ').trim()
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
  const byCandidateId = new Map(unique.map((item) => [item.id, item]))
  const selected = new Map<string, typeof unique[number]>()
  for (const item of unique) {
    const needed = [item]
    let previous = item
    while (previous.needs && byCandidateId.has(previous.needs)) {
      previous = byCandidateId.get(previous.needs)!
      needed.push(previous)
    }
    // A dependent phrase cannot be understood if its preceding point was rejected.
    if (previous.needs || needed.filter((row) => !selected.has(row.id)).length + selected.size > Math.max(0, limit)) continue
    for (const row of needed) selected.set(row.id, row)
  }
  // Preserve qualifying context, then restore the management presentation's order.
  return [...selected.values()].sort((a, b) => a.position - b.position)
    .map(({ rank: _rank, position: _position, needs: _needs, ...item }) => item)
}
