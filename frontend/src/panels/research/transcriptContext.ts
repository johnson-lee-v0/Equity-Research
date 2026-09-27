import type {
  TranscriptExchange,
  TranscriptResult,
  TranscriptSentence,
  TranscriptTurn,
} from './DocumentViews'

export type ReadingModel = ReturnType<typeof buildReadingModel>
export type EvidenceGroup = {
  id: string
  matches: TranscriptSentence[]
  turns: TranscriptTurn[]
  exchange?: TranscriptExchange
}
export type DiscussionExcerpt = {
  turn: TranscriptTurn
  label: string
  text: string
  sentenceId?: string | number
  highlight?: string
  highlightStart?: number
  includedSentenceIds: (string | number)[]
  fullContextRetained: boolean
  omittedBefore: boolean
  omittedAfter: boolean
}

/** Legacy records cannot recover omitted words; never claim reconstructed text is complete. */
export function buildReadingModel(result: TranscriptResult) {
  const context = result.reading_context
  const turns: TranscriptTurn[] = context?.turns?.length ? context.turns : []
  if (!turns.length) {
    for (const sentence of result.sentences) {
      const prior = turns.at(-1)
      if (
        prior &&
        prior.speaker === (sentence.speaker || 'Unattributed') &&
        prior.section === (sentence.section || 'Prepared remarks')
      ) {
        prior.text += ` ${sentence.text}`
        prior.sentence_ids.push(sentence.id)
      } else {
        turns.push({
          id: `legacy-turn-${turns.length + 1}`,
          speaker: sentence.speaker || 'Unattributed',
          role: sentence.role || 'unknown',
          section: sentence.section || 'Prepared remarks',
          text: sentence.text,
          sentence_ids: [sentence.id],
        })
      }
    }
  }
  const sentenceById = new Map(result.sentences.map((sentence) => [String(sentence.id), sentence]))
  const turnById = new Map(turns.map((turn) => [turn.id, turn]))
  const turnBySentence = new Map(
    turns.flatMap((turn) => turn.sentence_ids.map((id) => [String(id), turn] as const)),
  )
  const sentenceOffsets = new Map<string, { turnId: string; start: number; end: number }>()
  for (const turn of turns) {
    let cursor = 0
    for (const id of turn.sentence_ids) {
      const sentence = sentenceById.get(String(id))
      if (!sentence) continue
      const start = turn.text.indexOf(sentence.text, cursor)
      if (start < 0) continue
      const end = start + sentence.text.length
      sentenceOffsets.set(String(id), { turnId: turn.id, start, end })
      cursor = end
    }
  }
  const exchanges = context?.exchanges || []
  const exchangeBySentence = new Map(
    exchanges.flatMap((exchange) =>
      exchange.sentence_ids.map((id) => [String(id), exchange] as const),
    ),
  )
  return {
    turns,
    exchanges,
    sentenceById,
    turnById,
    turnBySentence,
    sentenceOffsets,
    exchangeBySentence,
    fullTextAvailable: Boolean(context?.full_text_available),
    transcriptText:
      context?.transcript_text ||
      turns.map((turn) => `${turn.speaker} (${roleLabel(turn.role)})\n${turn.text}`).join('\n\n'),
  }
}

export function roleLabel(role?: string, statementKind?: string) {
  return role === 'management'
    ? 'Management'
    : role === 'analyst'
      ? statementKind === 'analyst_question'
        ? 'Analyst question'
        : 'Analyst'
      : role === 'operator'
        ? 'Operator'
        : 'Role unverified'
}

export function groupEvidence(
  sentences: TranscriptSentence[],
  model: ReadingModel,
): EvidenceGroup[] {
  const groups = new Map<string, EvidenceGroup>()
  for (const sentence of sentences) {
    const turn = model.turnBySentence.get(String(sentence.id))
    const exchange = model.exchangeBySentence.get(String(sentence.id))
    const id = exchange?.id || turn?.id || `sentence-${sentence.id}`
    const existing = groups.get(id)
    if (existing) {
      if (!existing.matches.some((match) => String(match.id) === String(sentence.id)))
        existing.matches.push(sentence)
      continue
    }
    const includedIds = exchange
      ? new Set([
          ...exchange.question_turn_ids,
          ...exchange.answer_turn_ids,
          ...exchange.context_turn_ids,
        ])
      : new Set(turn ? [turn.id] : [])
    // Transcript order and all responses survive every theme, role and sentiment filter.
    groups.set(id, {
      id,
      matches: [sentence],
      exchange,
      turns: model.turns.filter((item) => includedIds.has(item.id)),
    })
  }
  return [...groups.values()]
}

export function negativeSentences(result: TranscriptResult, scope: string, query = '') {
  const needle = query.trim().toLocaleLowerCase()
  return result.sentences.filter(
    (sentence) =>
      sentence.sentiment.label === 'negative' &&
      (scope === 'all' || sentence.role === scope) &&
      (!needle ||
        `${sentence.text} ${sentence.speaker || ''}`.toLocaleLowerCase().includes(needle)),
  )
}

export function transcriptPageForSentence(
  model: ReadingModel,
  sentenceId: string | number,
  pageSize: number,
) {
  const turn = model.turnBySentence.get(String(sentenceId))
  return turn ? transcriptPageForTurn(model, turn.id, pageSize) : null
}

export function transcriptPageForTurn(model: ReadingModel, turnId: string, pageSize: number) {
  const index = model.turns.findIndex((item) => item.id === turnId)
  return index < 0 ? null : { turn: model.turns[index], page: Math.floor(index / pageSize) }
}

function hasMaterialQualifier(text: string) {
  const leadingCondition = /(?:^|[.!?]\s+)(?:["“]\s*)?(?:only\s+(?:if|in|when)|if|unless|assuming|provided|subject\s+to)\b/i
  const negatedClaim = /\b(?:not\s+(?:(?:our|the|a)\s+)?base\s+case|not\s+(?:expect|plan|seeing)|(?:do\s+not|don['’]t)\s+(?:expect|plan)|no\s+plans|haven['’]t\s+seen)\b/i
  return leadingCondition.test(text.trim()) || negatedClaim.test(text)
}

/** A quoted summary selects source text; it never infers what an answer means. */
export function discussionExcerpts(group: EvidenceGroup, model: ReadingModel): DiscussionExcerpt[] {
  const questions = new Set(group.exchange?.question_turn_ids || [])
  const answers = new Set(group.exchange?.answer_turn_ids || [])
  const selected = group.exchange
    ? group.turns.filter((turn) => questions.has(turn.id) || answers.has(turn.id))
    : group.turns
  return selected.map((turn) => {
    const sentences = turn.sentence_ids.map((id) => model.sentenceById.get(String(id)))
      .filter((sentence): sentence is TranscriptSentence => Boolean(sentence && model.sentenceOffsets.get(String(sentence.id))?.turnId === turn.id))
    const matched = sentences.find((sentence) => group.matches.some((match) => String(match.id) === String(sentence.id)))
    const substantive = sentences.find((sentence) => !/^(thank you|thanks|good (morning|afternoon)|sure|yes|okay|great)[.!\s]*$/i.test(sentence.text.trim()))
    const anchor = matched || substantive || sentences[0]
    const unalignedMatch = group.matches.some((match) => turn.sentence_ids.some((id) => String(id) === String(match.id)) && !model.sentenceOffsets.has(String(match.id)))
    let text = turn.text
    let start = 0
    let end = turn.text.length
    let fullContextRetained = unalignedMatch
    // Spoken asks can end in a period or have no analyzed sentence at all.
    // Preserve the complete question turn so every management reply has its ask.
    if (anchor && !unalignedMatch && !questions.has(turn.id)) {
      const index = sentences.indexOf(anchor)
      // Keep the preceding and following context around a management quote.
      const previous = sentences[index - 1] || anchor
      start = model.sentenceOffsets.get(String(previous.id))!.start
      const following = sentences[index + 1] || anchor
      end = model.sentenceOffsets.get(String(following.id))!.end
      text = turn.text.slice(start, end)
      const outside = [turn.text.slice(0, start), turn.text.slice(end)]
      if (turn.role === 'management' && outside.some(hasMaterialQualifier)) {
        // A clipped conditional or negation can reverse the apparent answer.
        // Retain this speaker turn rather than paraphrasing its implications.
        start = 0
        end = turn.text.length
        text = turn.text
        fullContextRetained = true
      }
    }
    return {
      turn,
      label: questions.has(turn.id) ? 'Analyst asked' : answers.has(turn.id) ? 'Management answered'
        : turn.role === 'management' ? 'Management said' : `${roleLabel(turn.role)} said`,
      text,
      sentenceId: unalignedMatch ? undefined : anchor?.id,
      highlight: matched && text.includes(matched.text) ? matched.text : undefined,
      highlightStart: matched && model.sentenceOffsets.get(String(matched.id))!.start >= start && model.sentenceOffsets.get(String(matched.id))!.end <= end
        ? model.sentenceOffsets.get(String(matched.id))!.start - start : undefined,
      includedSentenceIds: sentences.filter((sentence) => {
        const span = model.sentenceOffsets.get(String(sentence.id))!
        return span.start >= start && span.end <= end
      }).map((sentence) => sentence.id),
      fullContextRetained,
      omittedBefore: start > 0,
      omittedAfter: end < turn.text.length,
    }
  })
}

/** Prioritize complete Q&A discussions without changing transcript chronology. */
export function orderDiscussionGroups(groups: EvidenceGroup[], model: ReadingModel, questionsFirst: boolean): EvidenceGroup[] {
  const position = new Map(model.turns.map((turn, index) => [turn.id, index]))
  return groups.map((group, index) => ({ group, index })).sort((a, b) => {
    if (questionsFirst && Boolean(a.group.exchange) !== Boolean(b.group.exchange)) return a.group.exchange ? -1 : 1
    const aPosition = position.get(a.group.turns[0]?.id) ?? Number.MAX_SAFE_INTEGER
    const bPosition = position.get(b.group.turns[0]?.id) ?? Number.MAX_SAFE_INTEGER
    return aPosition - bPosition || a.index - b.index
  }).map(({ group }) => group)
}

export function discussionMatchesOutsideBrief(group: EvidenceGroup, excerpts: DiscussionExcerpt[]): TranscriptSentence[] {
  return group.matches.filter((match) => !excerpts.some((excerpt) =>
    excerpt.includedSentenceIds.some((id) => String(id) === String(match.id))))
}
