import type { EarningsTrendSeries } from '../panels/research/earningsTrendModel'
import type { SharedMemoryGraph, SharedMemoryNote } from '../panels/memoryGraphModel'
import { META_CALL_TRANSCRIPT } from './metaEarningsCall'
import { metaMarket, metaScenarios, metaEntry } from './metaValuation'

// Public-source META case. Authored from cited materials, never exported from the private ledger.
export const META_RELEASE = 'https://investor.atmeta.com/investor-news/press-release-details/2026/Meta-Reports-Second-Quarter-2026-Results/'
export const META_SLIDES = 'https://s21.q4cdn.com/399680738/files/doc_financials/2026/q2/Earnings-Presentation-Q2-2026.pdf'
export const MUSE_RELEASE = 'https://about.fb.com/news/2026/09/introducing-muse-personal-ai-agent/'
export const DEMO_AS_OF = '2026-09-26'
export const EARNINGS_REPORTED_AT = '2026-07-29'
export const EARNINGS_PERIOD_END = '2026-06-30'
export const MUSE_RELEASED_AT = '2026-09-08'
export const latestFinancials = {
  period: 'Q2 FY2026', revenue: 60.801, revenueGrowthPercent: 28,
  operatingMarginPercent: 31, priorOperatingMarginPercent: 43,
  costsAndExpenses: 42.026, expenseGrowthPercent: 55,
  operatingIncome: 18.775, operatingIncomeGrowthPercent: -8,
  dilutedEps: 6.18, priorDilutedEps: 7.14,
  capex: 31.078, cashPpe: 30.116, financeLeasePrincipal: 0.962,
  operatingCashFlow: 31.862, freeCashFlow: 0.784,
  yearToDateCapex: 50.918, annualCapexGuidance: [130, 145],
} as const
export const capex: EarningsTrendSeries = {
  id: 'quarterly_capex', label: 'Quarterly capital expenditure', unit: 'USD billions', frequency: 'quarterly',
  area_ids: ['capital'], basis: 'Meta reported capital expenditure including finance-lease principal. Each bar is one quarter, not year to date.',
  points: [['Q1 FY2025', 13.692], ['Q2 FY2025', 17.012], ['Q3 FY2025', 19.374], ['Q4 FY2025', 22.137], ['Q1 FY2026', 19.840], ['Q2 FY2026', 31.078]].map(([period, value]) => ({
    period: String(period), value: Number(value), kind: 'actual', url: `${META_SLIDES}#page=15`, title: 'Meta Q2 2026 earnings presentation, pages 9 and 15',
    published_at: EARNINGS_REPORTED_AT, source_method: 'issuer_earnings_presentation',
  })),
}
export const museThesis = {
  positive: 'Paid, repeat use could add revenue and make the infrastructure investment more productive.',
  opposing: 'Heavy usage could cost more to serve than users will pay; popularity alone does not prove profit.',
  evidence: 'Watch paid conversion, repeat use and serving costs, then check whether free cash flow improves.',
}
export const questions = [
  { title: 'Is growth turning into more profit?', answer: 'Q2 revenue rose 28% to $60.801 billion, but operating income fell 8%. Selling more did not mean earning more.', source: META_RELEASE },
  { title: 'Why is profit under pressure?', answer: 'Expenses rose 55%; operating margin fell from 43% to 31%. The question is whether future growth can outrun these costs.', context: 'Q2 expenses included $2.40 billion of legal charges and $1.18 billion of severance. Not all margin pressure reflects recurring AI costs.', source: META_RELEASE },
  { title: 'What is left after the building bill?', answer: 'Operating cash flow was $31.862 billion. CapEx of $31.078 billion left $0.784 billion of company-defined free cash flow.', source: `${META_SLIDES}#page=15` },
  { title: 'Could Muse help the investment pay off?', answer: 'The personal agent launched after Q2. Paid adoption could help, but its launch announcement supplies no standalone revenue, conversion or profit figures.', source: MUSE_RELEASE },
  { title: 'What would make this worth buying?', answer: `The base twelve-month scenario is $${metaScenarios[1].future.toFixed(2)}, versus the $${metaMarket.price.toFixed(2)} close on ${metaMarket.date}. The watchlist review price is $${metaEntry.price.toFixed(2)}; both are conditional calculations, not reported facts.`, context: 'Pricing below shows the sourced trailing earnings, tax distortions and analyst growth/multiple assumptions. Cash returns and Muse economics still need to improve.', source: metaMarket.source },
]

const definitions = [
  ['meta', 'META', 'company', 'Public research walkthrough as of September 26, 2026. Latest reported quarter: Q2 FY2026.'],
  ['release', 'Q2 2026 earnings release', 'source', 'Reported July 29, 2026; quarter ended June 30.'],
  ['call', 'Q2 2026 earnings call transcript', 'source', 'Original issuer transcript, July 29, 2026; 21 pages.'],
  ['pricing', 'META valuation and review price', 'opinion', `TTM GAAP EPS and explicit growth/multiple assumptions imply a base twelve-month price of $${metaScenarios[1].future.toFixed(2)}. Review threshold: $${metaEntry.price.toFixed(2)}; sourced close: $${metaMarket.price.toFixed(2)} on ${metaMarket.date}.`],
  ['slides', 'Q2 2026 earnings slides', 'source', 'Quarterly CapEx and cash-flow reconciliation: pages 9 and 15.'],
  ['capex', 'Q2 CapEx: $31.078 billion', 'fact', 'Cash PP&E $30.116 billion plus finance-lease principal $0.962 billion.'],
  ['growth', 'Growth is ahead of profit', 'fact', 'Revenue +28%; operating income −8%; operating margin 31%, versus 43% a year earlier.'],
  ['muse', 'Muse personal agent launch', 'source', 'Meta announced Muse on September 8, after the reported quarter. The launch does not establish standalone product revenue or profit.'],
  ['question', 'Will Muse earn more than it costs?', 'gap', 'Research question: measure paid adoption, repeat use and serving costs before assuming an investment return.'],
  ['view', 'Muse investment thesis', 'opinion', 'A product launch is a catalyst to investigate. Strong usage would still need to translate into cash returns.'],
  ['review', 'Q2 results + September product update', 'earnings', 'Revenue growth is not yet translating into comparable profit growth. The valuation assigns no standalone Muse earnings; revisit on evidence of cash returns.'],
] as const
export const demoNotes: SharedMemoryNote[] = definitions.map(([id, title, kind, body]) => {
  const url = id === 'call' ? META_CALL_TRANSCRIPT : id === 'pricing' ? metaMarket.source : id === 'slides' || id === 'capex' ? META_SLIDES : id === 'release' || id === 'growth' ? META_RELEASE : id === 'muse' ? MUSE_RELEASE : undefined
  const linkedIds = id === 'meta' ? definitions.filter(d => d[0] !== id).map(d => d[0]) : id === 'muse' ? ['meta', 'question'] : id === 'question' ? ['meta', 'muse'] : id === 'capex' ? ['meta', 'slides'] : ['meta']
  return { id, title, kind, ticker: 'META', path: `demo/${id}.md`, updated_at: DEMO_AS_OF, status: kind === 'opinion' ? 'interpretation' : 'public_source', excerpt: body, source_url: url,
    markdown: `# ${title}\n\n${body}\n\n[[META]]`, frontmatter: { namespace: 'demo', source_url: url || null }, links: linkedIds.map(target => ({ target, label: definitions.find(d => d[0] === target)![1] })) }
})
export const demoGraph: SharedMemoryGraph = { version: 'public-demo-2026-09-26', vault_path: 'Public META research', nodes: demoNotes, edges: [
  ...demoNotes.filter(n => n.id !== 'meta').map(n => ({ source: 'meta', target: n.id, kind: 'wikilink' })),
  { source: 'pricing', target: 'release', kind: 'evidence' }, { source: 'review', target: 'call', kind: 'analysis' }, { source: 'capex', target: 'slides', kind: 'evidence' }, { source: 'growth', target: 'release', kind: 'evidence' }, { source: 'view', target: 'question', kind: 'question' }, { source: 'muse', target: 'question', kind: 'catalyst' },
], tickers: ['META'], kinds: [...new Set(demoNotes.map(n => n.kind))], total_nodes: demoNotes.length, truncated: false }
