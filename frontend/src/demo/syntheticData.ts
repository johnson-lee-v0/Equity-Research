import type { SharedMemoryGraph, SharedMemoryNote } from '../panels/memoryGraphModel'

// Original teaching inputs, invented for this demo. They describe no real issuer,
// security, market quotation, analyst, historical result or portfolio.
export const example = {
  company: 'Cedar Workshop',
  label: 'Fictional Cedar Workshop',
  authoredAt: '2026-10-02',
  referencePrice: 50,
  annualEps: 3,
  defaultGrowthPercent: 5,
  defaultMultiple: 16,
  reviewDiscountPercent: 25,
  quarters: [
    { period: 'Year 1 · Q1', revenue: 80, operatingProfit: 12, operatingCash: 10, capitalSpending: 4 },
    { period: 'Year 1 · Q2', revenue: 88, operatingProfit: 13, operatingCash: 12, capitalSpending: 5 },
    { period: 'Year 1 · Q3', revenue: 96, operatingProfit: 14, operatingCash: 11, capitalSpending: 7 },
    { period: 'Year 1 · Q4', revenue: 104, operatingProfit: 13, operatingCash: 14, capitalSpending: 9 },
  ],
} as const

export function calculateExample(growthPercent: number, multiple: number) {
  if (!Number.isFinite(growthPercent) || !Number.isFinite(multiple) || growthPercent < -100 || multiple < 0) return null
  const futureEps = example.annualEps * (1 + growthPercent / 100)
  const today = example.annualEps * multiple
  const future = futureEps * multiple
  return { today, future, futureEps, changePercent: (future / example.referencePrice - 1) * 100 }
}

export const scenarios = [
  { name: 'Lower earnings / multiple', growth: -20, multiple: 12 },
  { name: 'Central teaching assumption', growth: 5, multiple: 16 },
  { name: 'Higher earnings / multiple', growth: 15, multiple: 20 },
].map(row => ({ ...row, ...calculateExample(row.growth, row.multiple)! }))

export const reviewThreshold = example.annualEps * example.defaultMultiple * (1 - example.reviewDiscountPercent / 100)
export const questions = [
  { title: 'What changed?', answer: 'Invented quarterly sales rise from 80 to 104 units of currency, while operating profit ends at 13. Sales growth alone does not show that profit margins improved.' },
  { title: 'What is left after investment?', answer: 'In the final invented quarter, operating cash of 14 less capital spending of 9 leaves 5. This simplified calculation omits many items a real company may report.' },
  { title: 'Which assumption matters?', answer: 'The teaching model applies one year of earnings growth and a chosen price-to-earnings multiple. A lower multiple can offset higher earnings.' },
  { title: 'What could go wrong?', answer: 'Demand could fall, costs could rise, and investment could fail to earn a return. The three examples do not cover all outcomes, including a total loss.' },
  { title: 'What would need checking?', answer: 'For a real company, independently verify current filings, accounting definitions, market prices, debt, dilution, risks and your own circumstances before making a decision.' },
] as const

const notes = [
  ['cedar', 'Fictional Cedar Workshop', 'company', 'An invented company used only to demonstrate a research workflow. It is not a real security or investment opportunity.'],
  ['sales', 'Invented sales trend', 'source', 'The four teaching quarters contain revenue of 80, 88, 96 and 104. These numbers were authored for the demonstration, not obtained from a company.'],
  ['cash', 'Simplified cash arithmetic', 'fact', 'Invented final-quarter operating cash of 14 minus capital spending of 9 equals 5. Real-world free-cash-flow definitions vary.'],
  ['assumptions', 'Growth and multiple assumptions', 'opinion', 'The default example applies 5% annual growth to invented EPS of 3 and a multiple of 16. These are teaching inputs, not forecasts.'],
  ['risk', 'What the example omits', 'gap', 'No debt model, dilution, tax forecast, liquidity, costs, dividends or probability model is included. The downside examples are not a worst-case limit.'],
  ['review', 'Example review trigger', 'opinion', 'A 25% discount to the invented current-EPS calculation gives an example review threshold of 36. This is neither an order nor a recommendation.'],
] as const

export const syntheticNotes: SharedMemoryNote[] = notes.map(([id, title, kind, body]) => ({
  id, title, kind, ticker: null, path: `demo/${id}.md`, updated_at: example.authoredAt,
  status: 'fictional_teaching_material', excerpt: body, markdown: `# ${title}\n\n${body}`,
  frontmatter: { namespace: 'demo', provenance: 'original fictional teaching material' },
  links: (id === 'cedar' ? notes.filter(note => note[0] !== id) : notes.filter(note => note[0] === 'cedar')).map(note => ({ target: note[0], label: note[1] })),
}))
export const syntheticGraph: SharedMemoryGraph = {
  version: 'fictional-demo-2026-10-02', vault_path: 'Fictional teaching notebook',
  nodes: syntheticNotes, edges: syntheticNotes.filter(note => note.id !== 'cedar').map(note => ({ source: 'cedar', target: note.id, kind: 'teaching_link' })),
  tickers: [], kinds: [...new Set(syntheticNotes.map(note => note.kind))], total_nodes: syntheticNotes.length, truncated: false,
}
