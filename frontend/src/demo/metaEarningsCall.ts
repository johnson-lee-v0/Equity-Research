// Source-backed public call index. No private workspace or raw transcript export.
export const META_CALL_TRANSCRIPT = 'https://s21.q4cdn.com/399680738/files/doc_financials/2026/q2/META-Q2-2026-Earnings-Call-Transcript.pdf'
const transcriptPage = (page: number) => `${META_CALL_TRANSCRIPT}#page=${page}`
export interface MetaCallQuote { text: string; speaker: string; page: number; sourceUrl: string }
export interface MetaCallDiscussion {
  id: string; themeId: string; title: string; question: string; answer: string
  /** Our evidence question, not an assertion that management promised it. */
  stillUnclear: string
  questionSpeaker: string; answerSpeaker: string
  questionPage: number; answerPage: number; questionSourceUrl: string; sourceUrl: string
  quote?: MetaCallQuote; caution: boolean; evidenceType: 'call_qa'
}
export const earningsCall = {
  period: 'Q2 FY2026', date: '2026-07-29', source: META_CALL_TRANSCRIPT,
  title: 'Meta Q2 FY2026 earnings call',
  summary: 'Every analyst exchange, grouped by investment question.',
  coverage: 'Seven analyst exchanges; twelve editorial topics. Summaries are paraphrases.',
  verifiedAt: '2026-09-27', sourceType: 'issuer_earnings_call_transcript',
  pageCount: 21, analystCount: 7, questionCount: 12, analysisType: 'editorial_reading',
} as const
export const callDiscussions: readonly MetaCallDiscussion[] = [
{
  "id": "ai-payoff",
  "themeId": "monetization",
  "title": "AI returns",
  "question": "Which AI business pays first?",
  "answer": "No winner chosen.",
  "stillUnclear": "Returns and timing.",
  "questionSpeaker": "Brian Nowak · Morgan Stanley",
  "answerSpeaker": "Mark Zuckerberg · CEO",
  "questionPage": 10,
  "answerPage": 10,
  "questionSourceUrl": transcriptPage(10),
  "sourceUrl": transcriptPage(10),
  "caution": false,
  "evidenceType": "call_qa"
},
{
  "id": "capex-uncertainty",
  "themeId": "funding",
  "title": "Future spending",
  "question": "What about 2027 spending?",
  "answer": "No specific forecast.",
  "stillUnclear": "Spending range.",
  "questionSpeaker": "Brian Nowak · Morgan Stanley",
  "answerSpeaker": "Susan Li · CFO",
  "questionPage": 10,
  "answerPage": 10,
  "questionSourceUrl": transcriptPage(10),
  "sourceUrl": transcriptPage(10),
  "caution": true,
  "evidenceType": "call_qa",
  "quote": {
    "text": "Infrastructure planning remains highly dynamic",
    "speaker": "Susan Li · CFO",
    "page": 10,
    "sourceUrl": transcriptPage(10)
  }
},
{
  "id": "enterprise-distribution",
  "themeId": "monetization",
  "title": "Business customers",
  "question": "How will enterprise sales work?",
  "answer": "Extend marketing; build new capabilities.",
  "stillUnclear": "Sales costs.",
  "questionSpeaker": "Eric Sheridan · Goldman Sachs",
  "answerSpeaker": "Mark Zuckerberg · CEO",
  "questionPage": 11,
  "answerPage": 11,
  "questionSourceUrl": transcriptPage(11),
  "sourceUrl": transcriptPage(11),
  "caution": false,
  "evidenceType": "call_qa"
},
{
  "id": "funding",
  "themeId": "funding",
  "title": "Funding",
  "question": "Who funds construction?",
  "answer": "Cash, borrowing and partnerships.",
  "stillUnclear": "Future obligations.",
  "questionSpeaker": "Eric Sheridan · Goldman Sachs",
  "answerSpeaker": "Susan Li · CFO",
  "questionPage": 11,
  "answerPage": 12,
  "questionSourceUrl": transcriptPage(11),
  "sourceUrl": transcriptPage(12),
  "caution": false,
  "evidenceType": "call_qa"
},
{
  "id": "consumer-agents",
  "themeId": "consumer",
  "title": "Consumer agents",
  "question": "Are consumers ready?",
  "answer": "Reliability matters; launch still forthcoming.",
  "stillUnclear": "Paid retention.",
  "questionSpeaker": "Mark Shmulik · Bernstein",
  "answerSpeaker": "Mark Zuckerberg · CEO",
  "questionPage": 12,
  "answerPage": 13,
  "questionSourceUrl": transcriptPage(12),
  "sourceUrl": transcriptPage(13),
  "caution": false,
  "evidenceType": "call_qa",
  "quote": {
    "text": "it needs to just work",
    "speaker": "Mark Zuckerberg · CEO",
    "page": 13,
    "sourceUrl": transcriptPage(13)
  }
},
{
  "id": "recommendations",
  "themeId": "growth",
  "title": "Recommendations",
  "question": "What improves recommendations?",
  "answer": "Better models, data and personalization.",
  "stillUnclear": "Incremental returns.",
  "questionSpeaker": "Douglas Anmuth · JPMorgan",
  "answerSpeaker": "Susan Li · CFO",
  "questionPage": 14,
  "answerPage": 14,
  "questionSourceUrl": transcriptPage(14),
  "sourceUrl": transcriptPage(14),
  "caution": false,
  "evidenceType": "call_qa"
},
{
  "id": "payback-delay",
  "themeId": "capacity",
  "title": "Delayed payback",
  "question": "Why buy and sell computing?",
  "answer": "Competing uses; facilities take time.",
  "stillUnclear": "Utilization timing.",
  "questionSpeaker": "Douglas Anmuth · JPMorgan",
  "answerSpeaker": "Mark Zuckerberg · CEO",
  "questionPage": 14,
  "answerPage": 15,
  "questionSourceUrl": transcriptPage(14),
  "sourceUrl": transcriptPage(15),
  "caution": true,
  "evidenceType": "call_qa",
  "quote": {
    "text": "not getting value out of them until they’re online",
    "speaker": "Mark Zuckerberg · CEO",
    "page": 16,
    "sourceUrl": transcriptPage(16)
  }
},
{
  "id": "lab-advantage",
  "themeId": "models",
  "title": "Competitive advantage",
  "question": "What advantage lasts?",
  "answer": "Distribution and learning from usage.",
  "stillUnclear": "Defensible economics.",
  "questionSpeaker": "Justin Post · Bank of America",
  "answerSpeaker": "Mark Zuckerberg · CEO",
  "questionPage": 16,
  "answerPage": 16,
  "questionSourceUrl": transcriptPage(16),
  "sourceUrl": transcriptPage(16),
  "caution": false,
  "evidenceType": "call_qa"
},
{
  "id": "model-scale",
  "themeId": "models",
  "title": "Model sizes",
  "question": "Why large and small models?",
  "answer": "Capability and efficiency both matter.",
  "stillUnclear": "Serving costs.",
  "questionSpeaker": "Ross Sandler · Barclays",
  "answerSpeaker": "Mark Zuckerberg · CEO",
  "questionPage": 17,
  "answerPage": 17,
  "questionSourceUrl": transcriptPage(17),
  "sourceUrl": transcriptPage(17),
  "caution": false,
  "evidenceType": "call_qa"
},
{
  "id": "open-models",
  "themeId": "models",
  "title": "Open models",
  "question": "Will models be open?",
  "answer": "A mixed approach continues.",
  "stillUnclear": "Release dates.",
  "questionSpeaker": "Ross Sandler · Barclays",
  "answerSpeaker": "Mark Zuckerberg · CEO",
  "questionPage": 17,
  "answerPage": 18,
  "questionSourceUrl": transcriptPage(17),
  "sourceUrl": transcriptPage(18),
  "caution": false,
  "evidenceType": "call_qa"
},
{
  "id": "model-independence",
  "themeId": "models",
  "title": "Own models",
  "question": "Why not use others’ models?",
  "answer": "Control and tailored capabilities.",
  "stillUnclear": "Relative returns.",
  "questionSpeaker": "Kenneth Gawrelski · Wells Fargo",
  "answerSpeaker": "Mark Zuckerberg · CEO",
  "questionPage": 18,
  "answerPage": 19,
  "questionSourceUrl": transcriptPage(18),
  "sourceUrl": transcriptPage(19),
  "caution": false,
  "evidenceType": "call_qa"
},
{
  "id": "capacity-constraints",
  "themeId": "capacity",
  "title": "Capacity constraints",
  "question": "Is capacity demand-led?",
  "answer": "Supply constrains today; retain flexibility.",
  "stillUnclear": "Longer-term demand.",
  "questionSpeaker": "Kenneth Gawrelski · Wells Fargo",
  "answerSpeaker": "Susan Li · CFO",
  "questionPage": 18,
  "answerPage": 21,
  "questionSourceUrl": transcriptPage(18),
  "sourceUrl": transcriptPage(21),
  "caution": true,
  "evidenceType": "call_qa"
}
]
export const callThemes = callDiscussions
export const callRisks = callDiscussions.filter(item => item.caution)
