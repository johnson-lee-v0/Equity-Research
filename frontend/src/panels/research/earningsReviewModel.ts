import type { PlainLanguageBriefs, TranscriptResult } from './DocumentViews'
import { groupEvidence, orderDiscussionGroups, type EvidenceGroup, type ReadingModel } from './transcriptContext'
import type { EarningsTrendSeries, EarningsTrendsResult } from './earningsTrendModel'

export const TREND_CATEGORIES = [
  { id: 'growth', label: 'Growth', empty: 'No growth history was collected for this review.' },
  { id: 'margins', label: 'Margins', empty: 'No comparable margin history was collected for this review.' },
  { id: 'cash', label: 'Earnings & cash', empty: 'No earnings or cash-flow history was collected for this review. Missing figures are not zero.' },
  { id: 'capital', label: 'CapEx & guidance', empty: 'No capital-spending history was collected for this review.' },
  { id: 'other', label: 'Other saved figures', empty: 'No other historical figures were saved.' },
] as const
export type EarningsTrendCategory = typeof TREND_CATEGORIES[number]['id']

/** Group existing observations for display; never turn a metric into another measure. */
export function earningsTrendCategory(series: EarningsTrendSeries): EarningsTrendCategory {
  const metric = `${series.id.replaceAll('_', ' ')} ${series.label}`
  if (/\b(capex|capital expenditure|capital spending|cash ppe|purchases of (?:property|pp&e))\b/i.test(metric)) return 'capital'
  if (series.area_ids.some(id => ['earnings', 'cash', 'cash_flow'].includes(id)) || /\b(eps|earnings|net income|cash flow|ebitda)\b/i.test(metric)) return 'cash'
  if (series.area_ids.includes('margins')) return 'margins'
  if (series.area_ids.includes('demand') || series.area_ids.includes('growth')) return 'growth'
  if (series.area_ids.includes('capital')) return 'cash'
  return 'other'
}

export function groupedEarningsTrends(result?: EarningsTrendsResult | null) {
  return TREND_CATEGORIES.map(category => ({ ...category, series: (result?.series ?? []).filter(series => earningsTrendCategory(series) === category.id) }))
}

export function relatedThemeTrends(result: EarningsTrendsResult | null | undefined, names: string[]) {
  const themeAreas: Record<string, string[]> = {
    'Demand and growth': ['demand', 'growth'],
    'Margins and costs': ['margins'],
    'Guidance and outlook': ['outlook'],
    'Capital and liquidity': ['capital', 'cash', 'earnings', 'cash_flow'],
  }
  const areas = new Set(names.flatMap(name => themeAreas[name] ?? []))
  return (result?.series ?? []).filter(series => series.area_ids.some(id => areas.has(id)))
}

export function savedDiscussionBrief(result: TranscriptResult, briefs: PlainLanguageBriefs | undefined, groupId: string) {
  return result.source_hash && briefs?.source_hash === result.source_hash ? briefs.discussions[groupId] : undefined
}

export function discussionAnswerCoverage(group: EvidenceGroup, status?: PlainLanguageBriefs['discussions'][string]['answer_status']) {
  if (status === 'not_a_question') return { label: 'Context', text: 'Prepared remarks, rather than an analyst question.' }
  if (!group.exchange) return { label: 'Context', text: 'No complete question-and-answer link is saved for this passage.' }
  if (status === 'partial') return { label: 'Still unclear', text: 'Some parts of the question were left unanswered.' }
  if (status === 'unclear') return { label: 'Still unclear', text: 'It is unclear whether the response answers the question.' }
  if (status === 'no_response' || !group.exchange.answered) return { label: 'Still unclear', text: 'No identifiable management answer was found in the saved discussion.' }
  if (status === 'answered') return { label: 'Answer coverage', text: 'The saved explanation marks the question answered.' }
  return { label: 'Answer coverage', text: 'Not assessed in the saved explanations.' }
}

type DiscussionBrief = PlainLanguageBriefs['discussions'][string]
function themePreview(group: EvidenceGroup, brief: DiscussionBrief, model: ReadingModel) {
  const matched = brief.bullets.map(bullet => ({ bullet, ids: group.matches.filter(sentence => bullet.quotes.some(quote => {
    const turn = model.turnById.get(quote.turn_id)
    const position = model.sentenceOffsets.get(String(sentence.id))
    if (!turn || !position || position.turnId !== quote.turn_id || !quote.quote.trim() || !Number.isInteger(quote.offset) || quote.offset < 0) return false
    if (turn.text.slice(quote.offset, quote.offset + quote.quote.length) !== quote.quote) return false
    return quote.offset < position.end && quote.offset + quote.quote.length > position.start
  })).map(sentence => String(sentence.id)) }))
  const questions = brief.bullets.filter(bullet => bullet.kind === 'question')
  const answers = brief.bullets.filter(bullet => bullet.kind === 'answer')
  const relevantQuestions = matched.filter(item => item.ids.length && item.bullet.kind === 'question').map(item => item.bullet)
  const relevantAnswers = matched.filter(item => item.ids.length && item.bullet.kind === 'answer').map(item => item.bullet)
  const relevantRemarks = matched.filter(item => item.ids.length && item.bullet.kind === 'remark').map(item => item.bullet)
  // A multipart exchange cannot safely pair an arbitrary first ask with a
  // different response. If only one side is mapped, retain the complete pair.
  const paired = (relevantQuestions.length > 0 || questions.length === 1) && (relevantAnswers.length > 0 || answers.length === 1)
  const selected = group.exchange
    ? [...(paired && relevantQuestions.length ? relevantQuestions : questions), ...(paired && relevantAnswers.length ? relevantAnswers : answers)]
    : relevantRemarks
  return {
    bullets: selected,
    answerMatches: new Set(matched.filter(item => item.bullet.kind === 'answer').flatMap(item => item.ids)).size,
    questionMatches: new Set(matched.filter(item => item.bullet.kind === 'question').flatMap(item => item.ids)).size,
    totalMatches: new Set(matched.flatMap(item => item.ids)).size,
  }
}

export function transcriptThemeOverview(result: TranscriptResult, model: ReadingModel, briefs?: PlainLanguageBriefs) {
  return result.themes.map((theme, index) => {
    const matches = theme.evidence.map(item => model.sentenceById.get(String(item.sentence_id))).filter(item => item !== undefined)
    const groups = orderDiscussionGroups(groupEvidence(matches, model), model, true)
    const candidates = groups.flatMap(group => {
      const brief = savedDiscussionBrief(result, briefs, group.id)
      if (!brief) return []
      const preview = themePreview(group, brief, model)
      return preview.totalMatches > 0 ? [{ group, brief, preview }] : []
    })
    const exchanges = candidates.filter(candidate => candidate.group.exchange)
    const ranked = (exchanges.length ? exchanges : candidates).sort((a, b) => b.preview.answerMatches - a.preview.answerMatches
      || b.preview.questionMatches - a.preview.questionMatches || b.preview.totalMatches - a.preview.totalMatches
      || b.group.matches.length - a.group.matches.length)
    const chosen = ranked[0]
    return { theme, index, groups, group: chosen?.group ?? groups[0], brief: chosen?.brief, previewBullets: chosen?.preview.bullets ?? [] }
  })
}
