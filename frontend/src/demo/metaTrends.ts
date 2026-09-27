import type { CapexGuidanceComparison, EarningsTrendPoint, EarningsTrendSeries, EarningsTrendSource, EarningsTrendsResult } from '../panels/research/earningsTrendModel'

// Public issuer facts only. No ledger, saved assessment or model-generated values.
export const META_TRENDS_AS_OF = '2026-09-26'
const ir = 'https://investor.atmeta.com/investor-news/press-release-details'
export const META_TREND_DECK = 'https://s21.q4cdn.com/399680738/files/doc_financials/2026/q2/Earnings-Presentation-Q2-2026.pdf'
const release = (year: number, slug: string, legacy = false) => `${ir}/${year}/${slug}/${legacy ? 'default.aspx' : ''}`
const source = (id: string, url: string, title: string, date: string): EarningsTrendSource => ({
  source_id: id, url, title, published_at: date, source_method: 'issuer_earnings_release',
})
const deck = (page: number): EarningsTrendSource => ({
  ...source(`meta-q2-2026-slides-p${page}`, `${META_TREND_DECK}#page=${page}`, `Meta Q2 2026 earnings presentation, page ${page}`, '2026-07-29'),
  source_method: 'issuer_earnings_presentation',
})
const reports = {
  q32020: source('meta-q3-2020', release(2020, 'Facebook-Reports-Third-Quarter-2020-Results', true), 'Facebook Q3 2020 results', '2020-10-29'),
  q32021: source('meta-q3-2021', release(2021, 'Facebook-Reports-Third-Quarter-2021-Results', true), 'Facebook Q3 2021 results', '2021-10-25'),
  fy2021: source('meta-fy-2021', release(2022, 'Meta-Reports-Fourth-Quarter-and-Full-Year-2021-Results', true), 'Meta FY2021 results and cash-flow reconciliation', '2022-02-02'),
  q32022: source('meta-q3-2022', release(2022, 'Meta-Reports-Third-Quarter-2022-Results', true), 'Meta Q3 2022 results', '2022-10-26'),
  fy2022: source('meta-fy-2022', release(2023, 'Meta-Reports-Fourth-Quarter-and-Full-Year-2022-Results', true), 'Meta FY2022 results and cash-flow reconciliation', '2023-02-01'),
  q32023: source('meta-q3-2023', release(2023, 'Meta-Reports-Third-Quarter-2023-Results', true), 'Meta Q3 2023 results', '2023-10-25'),
  fy2023: source('meta-fy-2023', release(2024, 'Meta-Reports-Fourth-Quarter-and-Full-Year-2023-Results-Initiates-Quarterly-Dividend', true), 'Meta FY2023 results and cash-flow reconciliation', '2024-02-01'),
  q32024: source('meta-q3-2024', release(2024, 'Meta-Reports-Third-Quarter-2024-Results', true), 'Meta Q3 2024 results', '2024-10-30'),
  fy2024: source('meta-fy-2024', release(2025, 'Meta-Reports-Fourth-Quarter-and-Full-Year-2024-Results'), 'Meta FY2024 results and cash-flow reconciliation', '2025-01-29'),
  q12025: source('meta-q1-2025', release(2025, 'Meta-Reports-First-Quarter-2025-Results'), 'Meta Q1 2025 results', '2025-04-30'),
  q32025: source('meta-q3-2025', release(2025, 'Meta-Reports-Third-Quarter-2025-Results'), 'Meta Q3 2025 results', '2025-10-29'),
  fy2025: source('meta-fy-2025', release(2026, 'Meta-Reports-Fourth-Quarter-and-Full-Year-2025-Results'), 'Meta FY2025 results and cash-flow reconciliation', '2026-01-28'),
  q12026: source('meta-q1-2026', release(2026, 'Meta-Reports-First-Quarter-2026-Results'), 'Meta Q1 2026 results', '2026-04-29'),
  q22026: source('meta-q2-2026', release(2026, 'Meta-Reports-Second-Quarter-2026-Results'), 'Meta Q2 2026 results', '2026-07-29'),
} as const

// All monetary operands here are USD millions; EPS alone is USD per share.
// Slide 4 supplies revenue/operating income/margin; slide 8 supplies GAAP EPS;
// slide 15 supplies standalone quarter cash flows (never YTD).
export const metaQuarterlyFacts = [
  { period: 'Q1 FY2025', end: '2025-03-31', revenue: 42314, priorRevenue: 36455, operatingIncome: 17555, margin: 41, eps: 6.43, operatingCash: 24026, cashPpe: 12941, leasePrincipal: 751, freeCash: 10334 },
  { period: 'Q2 FY2025', end: '2025-06-30', revenue: 47516, priorRevenue: 39071, operatingIncome: 20441, margin: 43, eps: 7.14, operatingCash: 25561, cashPpe: 16538, leasePrincipal: 474, freeCash: 8549 },
  { period: 'Q3 FY2025', end: '2025-09-30', revenue: 51242, priorRevenue: 40589, operatingIncome: 20535, margin: 40, eps: 1.05, operatingCash: 29999, cashPpe: 18829, leasePrincipal: 545, freeCash: 10625 },
  { period: 'Q4 FY2025', end: '2025-12-31', revenue: 59893, priorRevenue: 48385, operatingIncome: 24745, margin: 41, eps: 8.88, operatingCash: 36214, cashPpe: 21383, leasePrincipal: 754, freeCash: 14077 },
  { period: 'Q1 FY2026', end: '2026-03-31', revenue: 56311, priorRevenue: 42314, operatingIncome: 22872, margin: 41, eps: 10.44, operatingCash: 32226, cashPpe: 18997, leasePrincipal: 843, freeCash: 12386 },
  { period: 'Q2 FY2026', end: '2026-06-30', revenue: 60801, priorRevenue: 47516, operatingIncome: 18775, margin: 31, eps: 6.18, operatingCash: 31862, cashPpe: 30116, leasePrincipal: 962, freeCash: 784 },
] as const
type Quarter = typeof metaQuarterlyFacts[number]
const billions = (millions: number) => millions / 1000
const quarterlyPoint = (row: Quarter, value: number, page: number, extra: Partial<EarningsTrendPoint> = {}): EarningsTrendPoint => ({
  ...deck(page), period: row.period, period_end: row.end, kind: 'actual', value, ...extra,
})
const quarterlyAreas = {
  revenue: ['demand', 'growth'],
  revenue_yoy_growth: ['demand', 'growth'],
  operating_margin: ['margins'],
  diluted_eps: ['earnings'],
  operating_cash_flow: ['cash', 'capital'],
  free_cash_flow: ['cash', 'capital'],
  capex_quarterly: ['capital'],
} as const
const quarterlySeries = (id: keyof typeof quarterlyAreas, label: string, unit: string, basis: string, makePoint: (row: Quarter) => EarningsTrendPoint): EarningsTrendSeries => ({
  id, label, unit, basis, frequency: 'quarterly', area_ids: [...quarterlyAreas[id]], points: metaQuarterlyFacts.map(makePoint),
})
const leaseCapexBasis = 'cash_ppe_plus_finance_lease_principal'

// Preserve each closing release's reconciliation row. FY2021–23 amounts were
// net of PP&E proceeds; later releases label the row as purchases of PP&E.
// Substituting a gross PP&E XBRL row would change those earlier management totals.
export const metaAnnualCapexFacts = [
  { year: 2021, cashPpe: 18567, leasePrincipal: 677, total: 19244, ppeBasis: 'net of PP&E proceeds', source: reports.fy2021 },
  { year: 2022, cashPpe: 31186, leasePrincipal: 850, total: 32036, ppeBasis: 'net of PP&E proceeds', source: reports.fy2022 },
  { year: 2023, cashPpe: 27045, leasePrincipal: 1058, total: 28103, ppeBasis: 'net of PP&E proceeds', source: reports.fy2023 },
  { year: 2024, cashPpe: 37256, leasePrincipal: 1969, total: 39225, ppeBasis: 'purchases of PP&E as reported', source: reports.fy2024 },
  { year: 2025, cashPpe: 69691, leasePrincipal: 2524, total: 72215, ppeBasis: 'purchases of PP&E as reported', source: reports.fy2025 },
] as const
const annualActuals: EarningsTrendPoint[] = metaAnnualCapexFacts.map(row => ({
  ...row.source, period: `FY${row.year}`, period_end: `${row.year}-12-31`, kind: 'actual', value: billions(row.total),
  measure_basis: 'management_capex_including_finance_lease_principal',
  rationale: `PP&E: ${row.ppeBasis}. Includes finance-lease principal.`,
  definition_source: row.source,
  calculation: {
    formula: '(reported PP&E cash outflow + finance-lease principal) / 1000',
    inputs: { cash_ppe_usd_millions: row.cashPpe, finance_lease_principal_usd_millions: row.leasePrincipal },
    basis: row.ppeBasis,
  },
}))

export const metaTrendSeries: EarningsTrendSeries[] = [
  quarterlySeries('revenue', 'Revenue', 'USD billions', 'Consolidated GAAP revenue for each standalone quarter.', row => quarterlyPoint(row, billions(row.revenue), 4)),
  quarterlySeries('revenue_yoy_growth', 'Revenue growth versus last year', 'percent', 'Reported-currency revenue versus the same quarter one year earlier, calculated and rounded to a whole percent.', row => quarterlyPoint(row, Math.round((row.revenue / row.priorRevenue - 1) * 100), 4, {
    calculation: {
      formula: '(quarter revenue / same quarter prior-year revenue - 1) × 100', rounding: 'Nearest whole percent',
      inputs: { revenue_usd_millions: row.revenue, prior_revenue_usd_millions: row.priorRevenue, prior_source: row.period === 'Q1 FY2025' ? reports.q12025 : deck(4) },
    },
  })),
  quarterlySeries('operating_margin', 'Operating margin', 'percent', 'GAAP operating income divided by revenue, at the issuer’s whole-percent precision.', row => quarterlyPoint(row, row.margin, 4)),
  quarterlySeries('diluted_eps', 'Diluted earnings per share', 'USD per share', 'Reported GAAP diluted EPS for each quarter. Large tax items affect Q3 FY2025 and Q1 FY2026.', row => quarterlyPoint(row, row.eps, 8, {
    rationale: row.period === 'Q3 FY2025' ? 'Includes a $15.93bn non-cash tax charge.' : row.period === 'Q1 FY2026' ? 'Includes an $8.03bn income-tax benefit.' : undefined,
  })),
  quarterlySeries('operating_cash_flow', 'Cash from operations', 'USD billions', 'GAAP net cash provided by operating activities for the standalone quarter.', row => quarterlyPoint(row, billions(row.operatingCash), 15)),
  quarterlySeries('free_cash_flow', 'Free cash flow', 'USD billions', 'Meta’s non-GAAP measure: operating cash less reported PP&E purchases and finance-lease principal.', row => quarterlyPoint(row, billions(row.freeCash), 15, {
    measure_basis: 'company_defined_non_gaap_free_cash_flow',
    calculation: { formula: '(operating cash - cash PP&E - finance-lease principal) / 1000', inputs: { operating_cash_usd_millions: row.operatingCash, cash_ppe_usd_millions: row.cashPpe, finance_lease_principal_usd_millions: row.leasePrincipal } },
  })),
  quarterlySeries('capex_quarterly', 'Quarterly capital expenditure', 'USD billions', 'Cash PP&E plus finance-lease principal under Meta’s stated definition. Each bar is one quarter.', row => quarterlyPoint(row, billions(row.cashPpe + row.leasePrincipal), 15, {
    measure_basis: leaseCapexBasis, definition_source: deck(9),
    calculation: {
      formula: '(cash PP&E + finance-lease principal) / 1,000,000,000',
      inputs: [
        { ...deck(15), tag: 'PaymentsToAcquirePropertyPlantAndEquipment', value: row.cashPpe * 1_000_000, unit: 'USD' },
        { ...deck(15), tag: 'FinanceLeasePrincipalPayments', value: row.leasePrincipal * 1_000_000, unit: 'USD' },
      ],
    },
  })),
  {
    id: 'capex', label: 'Annual capital expenditure', unit: 'USD billions', frequency: 'annual', area_ids: ['capital'],
    basis: 'Five completed years under each release’s management CapEx definition, including finance-lease principal. Earlier PP&E rows are net of proceeds. FY2026 is guidance.',
    points: [...annualActuals, {
      ...reports.q22026, period: 'FY2026', period_end: '2026-12-31', kind: 'guidance', value: 137.5, low: 130, high: 145,
      measure_basis: 'management_capex_including_finance_lease_principal',
      rationale: 'July 29 outlook. The midpoint is a display convention; full-year actual spending is not yet reported.',
    }],
  },
]

type Guidance = EarningsTrendSource & { low: number; high: number }
const guidance = (report: EarningsTrendSource, low: number, high: number, qualifier?: 'approximately'): Guidance => ({
  ...report, low, high, qualifier, measure_basis: 'management_capex_including_finance_lease_principal',
})
const capturedGuidance = [
  { year: 2021, initial: guidance(reports.q32020, 21, 23), revisions: [guidance(reports.q32021, 19, 19, 'approximately')] },
  { year: 2022, initial: guidance(reports.q32021, 29, 34), revisions: [guidance(reports.q32022, 32, 33)] },
  { year: 2023, initial: guidance(reports.q32022, 34, 39), revisions: [guidance(reports.fy2022, 30, 33), guidance(reports.q32023, 27, 29)] },
  { year: 2024, initial: guidance(reports.q32023, 30, 35), revisions: [guidance(reports.fy2023, 30, 37), guidance(reports.q32024, 38, 40)] },
  { year: 2025, initial: guidance(reports.fy2024, 60, 65), revisions: [guidance(reports.q12025, 64, 72), guidance(reports.q32025, 70, 72)] },
  { year: 2026, initial: guidance(reports.fy2025, 115, 135), revisions: [guidance(reports.q12026, 125, 145), guidance(reports.q22026, 130, 145)] },
]
const comparisons: CapexGuidanceComparison[] = capturedGuidance.map(row => {
  const observed = annualActuals.find(point => point.period === `FY${row.year}`)
  const actual = observed?.value ?? null
  const midpoint = (row.initial.low + row.initial.high) / 2
  const variance = actual === null ? undefined : Number((actual - midpoint).toFixed(3))
  return {
    period: `FY${row.year}`, initial_low: row.initial.low, initial_high: row.initial.high, actual,
    initiality: 'Earliest captured dated outlook; not a claim that every earlier disclosure was searched.',
    as_of: META_TRENDS_AS_OF, initial_source: row.initial, actual_source: observed,
    revisions: row.revisions, variance, variance_pct: variance === undefined ? undefined : variance / midpoint * 100,
    within_range: actual === null ? undefined : actual >= row.initial.low && actual <= row.initial.high,
  }
})

export const metaCapexGuidance: NonNullable<EarningsTrendsResult['capex_guidance']> = {
  comparisons,
  summary: 'Against these early ranges, two completed years finished below, one within and two above. This does not establish a consistent tendency to underestimate.',
  coverage: 'Selected dated revisions, not an exhaustive forecast archive. FY2026 actual is not yet available. Differences compare actual spending with the early range midpoint, in USD billions.',
  explanations: [
    { ...reports.fy2022, period: 'FY2023', text: 'Management reduced construction plans while moving to a more cost-efficient data-center design.', interpretation: 'Explains that forecast reduction; it does not establish every cause of the final underspend.' },
    { ...reports.fy2023, period: 'FY2024', text: 'The higher forecast reflected changing estimates of AI capacity needs.', interpretation: 'A dated reason for raising the outlook.' },
    { ...reports.q12025, period: 'FY2025', text: 'Management cited added AI data-center investment and higher expected hardware costs.', interpretation: 'A reason for the April increase, not proof of intentional underestimation.' },
    { ...reports.q12026, period: 'FY2026', text: 'Higher component prices and additional data-center costs raised the range.', interpretation: 'An outlook revision; the year remains incomplete.' },
  ],
}

export const metaTrendNotes: { id: string; text: string; sourceUrl: string; period?: string }[] = [
  { id: 'quarter-basis', text: 'The six-quarter charts use standalone quarters. FY2026 first-half CapEx is $50.918bn; the Q2 bar is $31.078bn.', sourceUrl: `${META_TREND_DECK}#page=15` },
  { id: 'eps-tax-charge', period: 'Q3 FY2025', text: 'GAAP EPS of $1.05 includes a $15.93bn non-cash tax charge. It is kept in the historical series.', sourceUrl: `${META_TREND_DECK}#page=8` },
  { id: 'eps-tax-benefit', period: 'Q1 FY2026', text: 'GAAP EPS of $10.44 includes an $8.03bn tax benefit. The earnings spike is not all operating improvement.', sourceUrl: `${META_TREND_DECK}#page=8` },
  { id: 'margin-context', period: 'Q2 FY2026', text: 'Q2 costs include $2.40bn of legal charges and $1.18bn of severance; the margin chart retains reported GAAP results.', sourceUrl: reports.q22026.url! },
  { id: 'annual-basis', text: 'Annual CapEx reconciles each closing release’s PP&E row plus lease principal. FY2021–23 use net PP&E spending, so a gross PP&E row cannot substitute for it.', sourceUrl: reports.fy2023.url! },
  { id: 'guidance-coverage', text: 'Forecast revisions shown are selected snapshots. Causes of the FY2021 and FY2022 final differences were not established here.', sourceUrl: reports.q32021.url! },
]

export const metaTrends: EarningsTrendsResult = {
  version: 'public-meta-trends.1', status: 'partial', as_of: META_TRENDS_AS_OF,
  series: metaTrendSeries, capex_guidance: metaCapexGuidance,
  sources: [deck(4), deck(8), deck(9), deck(15), ...Object.values(reports)],
  gaps: [
    'The dated guidance archive is selective; intermediate revisions may be omitted.',
    'The causes of the FY2021 and FY2022 final guidance differences were not established in the sources reviewed.',
    'FY2026 full-year actual is unreported as of the cutoff; no forecast is presented as an actual.',
  ],
}
