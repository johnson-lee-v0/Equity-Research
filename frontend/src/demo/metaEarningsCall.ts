// Public-source editorial analysis, authored independently of the private ledger.
// Keep excerpts brief in aggregate; link the issuer PDF for the full exchanges.
export const META_CALL_TRANSCRIPT = 'https://s21.q4cdn.com/399680738/files/doc_financials/2026/q2/META-Q2-2026-Earnings-Call-Transcript.pdf'

const transcriptPage = (page: number) => `${META_CALL_TRANSCRIPT}#page=${page}`

export interface MetaCallQuote {
  text: string
  speaker: string
  page: number
  sourceUrl: string
}

export interface MetaCallDiscussion {
  id: string
  title: string
  question: string
  answer: string
  questionSpeaker: string
  answerSpeaker: string
  /** Editorial investment check, not a statement attributed to management. */
  whyItMatters: string
  quote?: MetaCallQuote
  /** One-based PDF page numbers, independently checked against the issuer PDF. */
  questionPage: number
  answerPage: number
  questionSourceUrl: string
  sourceUrl: string
  evidenceType: 'call_qa'
}

export const earningsCall = {
  period: 'Q2 FY2026',
  date: '2026-07-29',
  source: META_CALL_TRANSCRIPT,
  title: 'Meta Q2 FY2026 earnings call',
  summary: 'Management sees several AI revenue paths, but spending and payback remain uncertain.',
  coverage: 'Selected Q&A from Meta’s complete issuer transcript; reader summaries are paraphrases.',
  verifiedAt: '2026-09-26',
  sourceType: 'issuer_earnings_call_transcript',
  pageCount: 21,
  analysisType: 'editorial_reading',
  // The subsequent September 8 personal-agent release belongs to the separately
  // sourced catalyst. July remarks about Muse models are not that product launch.
} as const

export const callThemes: readonly MetaCallDiscussion[] = [
  {
    id: 'ai-payoff',
    title: 'Which AI bet pays first?',
    question: 'Which new business will show returns first?',
    answer: 'Zuckerberg did not pick a winner; he expected AI services to earn better margins than renting computers.',
    questionSpeaker: 'Brian Nowak · Morgan Stanley',
    answerSpeaker: 'Mark Zuckerberg · CEO',
    whyItMatters: 'Ask for revenue and margin evidence.',
    questionPage: 10,
    answerPage: 10,
    questionSourceUrl: transcriptPage(10),
    sourceUrl: transcriptPage(10),
    evidenceType: 'call_qa',
  },
  {
    id: 'funding',
    title: 'How is the build funded?',
    question: 'Where will investment money come from?',
    answer: 'Li cited operating cash, longer-term borrowing and partnerships.',
    questionSpeaker: 'Eric Sheridan · Goldman Sachs',
    answerSpeaker: 'Susan Li · CFO',
    whyItMatters: 'Watch interest costs and partnership obligations.',
    questionPage: 11,
    answerPage: 12,
    questionSourceUrl: transcriptPage(11),
    sourceUrl: transcriptPage(12),
    evidenceType: 'call_qa',
  },
  {
    id: 'consumer-agents',
    title: 'Will ordinary people use agents?',
    question: 'Are consumers ready for personal agents?',
    answer: 'Zuckerberg said reliability matters; the consumer product was still forthcoming.',
    questionSpeaker: 'Mark Shmulik · Bernstein',
    answerSpeaker: 'Mark Zuckerberg · CEO',
    whyItMatters: 'Watch whether people keep using and paying.',
    quote: {
      text: 'it needs to just work',
      speaker: 'Mark Zuckerberg · CEO',
      page: 13,
      sourceUrl: transcriptPage(13),
    },
    questionPage: 12,
    answerPage: 13,
    questionSourceUrl: transcriptPage(12),
    sourceUrl: transcriptPage(13),
    evidenceType: 'call_qa',
  },
]

// Selected caution language, not a quantitative sentiment score or prediction.
export const callRisks: readonly MetaCallDiscussion[] = [
  {
    id: 'capex-uncertainty',
    title: 'Next year’s bill remains uncertain',
    question: 'What might 2027 capital spending be?',
    answer: 'Li declined a specific forecast and emphasized flexibility.',
    questionSpeaker: 'Brian Nowak · Morgan Stanley',
    answerSpeaker: 'Susan Li · CFO',
    whyItMatters: 'Test pricing against several spending outcomes.',
    quote: {
      text: 'Infrastructure planning remains highly dynamic',
      speaker: 'Susan Li · CFO',
      page: 10,
      sourceUrl: transcriptPage(10),
    },
    questionPage: 10,
    answerPage: 10,
    questionSourceUrl: transcriptPage(10),
    sourceUrl: transcriptPage(10),
    evidenceType: 'call_qa',
  },
  {
    id: 'payback-delay',
    title: 'Spending comes before payback',
    question: 'Why sell computing capacity while buying more?',
    answer: 'Zuckerberg described competing uses and delays before new facilities generate value.',
    questionSpeaker: 'Douglas Anmuth · JPMorgan',
    answerSpeaker: 'Mark Zuckerberg · CEO',
    whyItMatters: 'Check when spending becomes usable capacity.',
    quote: {
      text: 'not getting value out of them until they’re online',
      speaker: 'Mark Zuckerberg · CEO',
      page: 16,
      sourceUrl: transcriptPage(16),
    },
    questionPage: 14,
    answerPage: 15,
    questionSourceUrl: transcriptPage(14),
    sourceUrl: transcriptPage(15),
    evidenceType: 'call_qa',
  },
]

// Short aliases for consumers that render the two discussion groups together.
export const themes = callThemes
export const risks = callRisks
