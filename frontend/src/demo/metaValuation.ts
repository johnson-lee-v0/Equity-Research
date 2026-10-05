import type { ValuationResearchContext } from '../types'

// Manually checked public disclosures. This module never reads the private ledger.
export const META_VALUATION_AS_OF = '2026-09-26'
export const metaValuationSources = {
  q2: 'https://investor.atmeta.com/investor-news/press-release-details/2026/Meta-Reports-Second-Quarter-2026-Results/',
  q1: 'https://investor.atmeta.com/investor-news/press-release-details/2026/Meta-Reports-First-Quarter-2026-Results/',
  fy2025: 'https://investor.atmeta.com/investor-news/press-release-details/2026/Meta-Reports-Fourth-Quarter-and-Full-Year-2025-Results/',
  q3_2025: 'https://investor.atmeta.com/investor-news/press-release-details/2025/Meta-Reports-Third-Quarter-2025-Results/',
  filing: 'https://www.sec.gov/Archives/edgar/data/1326801/000162828026050705/meta-20260630.htm',
  price: 'https://stockanalysis.com/stocks/meta/history/',
  ratios: 'https://stockanalysis.com/stocks/meta/financials/ratios/',
} as const

// Financial statements are in USD millions; convert totals to billions once.
// Use the published H1 totals rather than silently changing a source's rounded total.
export const metaReported = {
  fy2025: { revenueMillions: 200966, operatingIncomeMillions: 83276, daMillions: 18616, dilutedEps: 23.49 },
  h1_2025: { revenueMillions: 89830, operatingIncomeMillions: 37997, daMillions: 8242, dilutedEps: 13.56 },
  h1_2026: { revenueMillions: 117111, operatingIncomeMillions: 41647, daMillions: 12355, dilutedEps: 16.62 },
  q3_2025: { dilutedEps: 1.05, taxChargePerShare: 6.20, epsExcludingTaxCharge: 7.25 },
  q4_2025: { dilutedEps: 8.88 },
  q1_2026: { dilutedEps: 10.44, taxBenefitPerShare: 3.13 },
  q2_2026: { dilutedEps: 6.18, dilutedWeightedAverageSharesMillions: 2566 },
  balance: { cashMillions: 15462, marketableSecuritiesMillions: 74798, longTermDebtMillions: 83664, operatingLeasesCurrentMillions: 2425, operatingLeasesNoncurrentMillions: 26229, stockholdersEquityMillions: 261221, date: '2026-06-30' },
  shares: { classA: 2205128509, classB: 342377716, date: '2026-07-24' },
} as const

const trailing = (key: 'revenueMillions' | 'operatingIncomeMillions' | 'daMillions') =>
  (metaReported.fy2025[key] + metaReported.h1_2026[key] - metaReported.h1_2025[key]) / 1000
const round = (value: number, places = 6) => Number(value.toFixed(places))
export const metaTtm = {
  period: '2025-07-01 to 2026-06-30',
  periodEnd: '2026-06-30',
  dilutedEps: round(metaReported.q3_2025.dilutedEps + metaReported.q4_2025.dilutedEps + metaReported.q1_2026.dilutedEps + metaReported.q2_2026.dilutedEps),
  revenueBn: trailing('revenueMillions'),
  operatingIncomeBn: trailing('operatingIncomeMillions'),
  depreciationAmortizationBn: trailing('daMillions'),
  ebitdaBn: round(trailing('operatingIncomeMillions') + trailing('daMillions')),
  epsBasis: 'Sum of four reported quarterly diluted EPS figures. Rounded EPS and changing quarterly share weights make this a trailing approximation, not a separately reported annual EPS.',
  ebitdaBasis: 'Calculated operating income plus cash-flow depreciation and amortization; no stock compensation, legal-cost or severance addbacks. Not an issuer-reported adjusted EBITDA.',
  taxNote: 'GAAP earnings retain the Q3 2025 tax charge ($6.20 per share) and Q1 2026 tax benefit ($3.13 per share). Growing this baseline does not normalize those items.',
} as const

export const metaMarket = {
  price: 751.66,
  date: '2026-09-25',
  source: metaValuationSources.price,
  priceBasis: 'Regular-session Nasdaq close in USD; fixed public snapshot, not a live quote.',
  marketCapBn: 1914.859,
  enterpriseValueBn: 1936.917,
  valuationSource: metaValuationSources.ratios,
  sharesBn: (metaReported.shares.classA + metaReported.shares.classB) / 1e9,
  sharesDate: metaReported.shares.date,
  sharesSource: metaValuationSources.filing,
  impliedSharesBn: 1914.859 / 751.66,
  sharesBasis: 'Class A plus Class B shares outstanding on the 10-Q cover. Both classes have identical dividend and liquidation rights. This point-in-time count is held fixed in the scenario; it is not the diluted weighted-average EPS denominator.',
} as const

export const metaEnterpriseBridge = {
  netClaimsBn: round(metaMarket.enterpriseValueBn - metaMarket.marketCapBn),
  source: metaValuationSources.ratios,
  date: metaMarket.date,
  basis: 'Provider enterprise value minus provider market capitalization. Hold this aggregate deduction fixed; do not assume missing preferred or minority claims are zero.',
  reconciliation: 'The $22.058bn aggregate equals reported long-term debt $83.664bn plus operating leases $28.654bn minus cash and marketable securities $90.260bn. This arithmetic agreement does not independently verify every provider adjustment or finance-lease classification.',
  limitation: 'Provider-basis comparison, not an independently audited debt-to-equity bridge. Operating-lease treatment and EBITDA definitions can differ across providers and peers. Future borrowing, lease changes, dilution and cash spending are not projected.',
  preferredClaimsBn: null,
  minorityClaimsBn: null,
} as const

export type MetaValuationMethod = 'P/E' | 'P/S' | 'EV/EBITDA' | 'P/book' | 'P/NAV'
export type MetaValuationMethodInfo = {
  key: MetaValuationMethod
  label: string
  baseline: number | null
  unit: string
  period: string
  sourceUrls: string[]
  multiple: number | null
  growth: number
  formula: string
  methodReason: string
  assumptions: string[]
  available: boolean
  unavailableReason?: string
}
const heldShares = 'Hold the July 24 reported common-share count fixed. No forecast of buybacks or new shares.'
const forwardBasis = 'Apply annual growth once for a 12-month holding horizon. The target is a sensitivity of the latest trailing baseline, not analyst consensus or company guidance.'
const reportedFlowSources = [metaValuationSources.fy2025, metaValuationSources.q2]
export const valuationMethods: MetaValuationMethodInfo[] = [
  { key: 'P/E', label: 'Trailing diluted earnings per share', baseline: metaTtm.dilutedEps, unit: 'USD per diluted share', period: metaTtm.period,
    sourceUrls: [metaValuationSources.q3_2025, metaValuationSources.fy2025, metaValuationSources.q1, metaValuationSources.q2], multiple: 26, growth: 8,
    formula: 'Trailing diluted EPS × (1 + annual growth)^(months / 12) × selected P/E', methodReason: 'Useful for Meta’s positive earnings, while the unusual tax items require extra care.',
    assumptions: [metaTtm.epsBasis, metaTtm.taxNote, forwardBasis, 'The published defaults of 26× and 8% growth are analysis assumptions, not reported facts.'], available: true },
  { key: 'P/S', label: 'Trailing revenue', baseline: metaTtm.revenueBn, unit: 'USD billions', period: metaTtm.period,
    sourceUrls: [...reportedFlowSources, metaValuationSources.filing], multiple: 8, growth: 8,
    formula: 'Trailing revenue × (1 + annual growth)^(months / 12) × selected P/S ÷ shares', methodReason: 'A revenue cross-check. It does not establish that revenue growth creates profit.',
    assumptions: [heldShares, forwardBasis, 'The published defaults of 8× and 8% revenue growth are analysis assumptions. Profit margins are not inferred.'], available: true },
  { key: 'EV/EBITDA', label: 'Calculated trailing EBITDA', baseline: metaTtm.ebitdaBn, unit: 'USD billions', period: metaTtm.period,
    sourceUrls: [...reportedFlowSources, metaValuationSources.filing, metaValuationSources.ratios], multiple: 17, growth: 8,
    formula: '(Calculated EBITDA × growth factor × selected EV/EBITDA − provider net claims) ÷ shares', methodReason: 'A provider-basis operating comparison. CapEx is not deducted from EBITDA, so it needs the cash-flow review alongside it.',
    assumptions: [metaTtm.ebitdaBasis, metaEnterpriseBridge.basis, metaEnterpriseBridge.reconciliation, metaEnterpriseBridge.limitation, heldShares, forwardBasis, 'The published defaults of 17× and 8% EBITDA growth are analysis assumptions.'], available: true },
  { key: 'P/book', label: 'Reported stockholders’ equity', baseline: metaReported.balance.stockholdersEquityMillions / 1000, unit: 'USD billions', period: metaReported.balance.date,
    sourceUrls: [metaValuationSources.q2, metaValuationSources.filing], multiple: 7, growth: 8,
    formula: 'Reported book equity × growth factor × selected P/book ÷ shares', methodReason: 'A separate accounting comparison; Meta’s unrecorded intangible franchise makes book value a limited primary valuation tool.',
    assumptions: [heldShares, 'Book equity is not a fair-value appraisal of assets or NAV.', forwardBasis, 'The published defaults of 7× and 8% book-equity growth are analysis assumptions.'], available: true },
  { key: 'P/NAV', label: 'Net asset value', baseline: null, unit: 'USD billions', period: META_VALUATION_AS_OF,
    sourceUrls: [metaValuationSources.filing], multiple: null, growth: 0,
    formula: 'Appraised equity NAV × selected P/NAV ÷ shares', methodReason: 'No supported fair-value asset appraisal was collected for Meta; book equity cannot fill this gap.',
    assumptions: [], available: false, unavailableReason: 'Unavailable for META: no sourced equity NAV. Use the separately labeled P/book comparison if that accounting basis is useful.' },
]

export function calculateMetaPrice(method: MetaValuationMethod, growthPercent: number, multiple: number, horizonMonths = 12) {
  const info = valuationMethods.find(item => item.key === method)
  const empty = (reason: string) => ({ today: null, future: null, upsidePercent: null, reason })
  if (!info?.available || info.baseline === null) return empty(info?.unavailableReason || 'Unsupported valuation method.')
  if (![growthPercent, multiple, horizonMonths].every(Number.isFinite) || growthPercent <= -100 || multiple <= 0 || horizonMonths < 0) return empty('Enter finite assumptions, positive multiples and a nonnegative holding horizon; growth must exceed −100%.')
  const toPrice = (baseline: number) => method === 'P/E' ? baseline * multiple :
    (baseline * multiple - (method === 'EV/EBITDA' ? metaEnterpriseBridge.netClaimsBn : 0)) / metaMarket.sharesBn
  const today = toPrice(info.baseline)
  const future = toPrice(info.baseline * (1 + growthPercent / 100) ** (horizonMonths / 12))
  if (![today, future].every(Number.isFinite)) return empty('These assumptions exceed the calculation range.')
  if (today < 0 || future < 0) return empty('The selected enterprise value is below the fixed claims on the business. This scenario cannot support a positive equity price; review the multiple and claims rather than treating a negative result as a share price.')
  return { today, future, upsidePercent: (future / metaMarket.price - 1) * 100, reason: undefined }
}

export function metaEarningsBridge(growthPercent = 8): NonNullable<ValuationResearchContext['earnings_bridge']> {
  const valid = Number.isFinite(growthPercent) && growthPercent > -100
  const factor = 1 + growthPercent / 100
  const q3 = valid ? round(metaReported.q3_2025.epsExcludingTaxCharge * factor) : null
  const q4 = valid ? round(metaReported.q4_2025.dilutedEps * factor) : null
  return {
    fiscal_year: 2026, currency: 'USD', unit: 'Diluted EPS',
    quarters: [
      { period: 'Q1 FY2026', kind: 'reported', value: 10.44, period_end: '2026-03-31', source_refs: [metaValuationSources.q1], rationale: 'Reported GAAP EPS, including the $3.13 per-share tax benefit.' },
      { period: 'Q2 FY2026', kind: 'reported', value: 6.18, period_end: '2026-06-30', source_refs: [metaValuationSources.q2], rationale: 'Reported GAAP EPS.' },
      { period: 'Q3 FY2026', kind: valid ? 'projection' : 'missing', value: q3, period_end: '2026-09-30', source_refs: [metaValuationSources.q3_2025], rationale: `Analysis assumption: prior Q3 EPS excluding its disclosed tax charge ($1.05 + $6.20 = $7.25), grown ${growthPercent}%. This does not repeat the exceptional charge and is not reported 2026 EPS or guidance.` },
      { period: 'Q4 FY2026', kind: valid ? 'projection' : 'missing', value: q4, period_end: '2026-12-31', source_refs: [metaValuationSources.fy2025], rationale: `Analysis assumption: prior Q4 reported EPS $8.88, grown ${growthPercent}%; not reported 2026 EPS or guidance.` },
    ],
    reported_total: 16.62,
    projected_total: q3 === null || q4 === null ? null : round(q3 + q4),
    full_year_total: q3 === null || q4 === null ? null : round(16.62 + q3 + q4),
    method: 'Two reported quarters plus two explicit projections. Q3 uses the issuer’s prior-year EPS excluding its unusual tax charge; Q1 actual retains its tax benefit. Summed quarterly EPS is an approximation because share weights can change.',
    coverage_note: `Only Q1 and Q2 were reported as of ${META_VALUATION_AS_OF}. This calendar-FY2026 estimate is separate from the pricing control’s 12-month trailing-earnings sensitivity. Neither is company EPS guidance.`,
  }
}

export type MetaBaselineFact = { label: string; value: number; unit: string; period: string; basis: string; sourceUrls: string[] }
export const metaBaselineFacts: MetaBaselineFact[] = [
  { label: 'Last market close', value: metaMarket.price, unit: 'USD per share', period: metaMarket.date, basis: metaMarket.priceBasis, sourceUrls: [metaMarket.source] },
  { label: 'Trailing diluted EPS', value: metaTtm.dilutedEps, unit: 'USD per share', period: metaTtm.period, basis: '1.05 + 8.88 + 10.44 + 6.18; GAAP, including unusual tax items.', sourceUrls: valuationMethods[0].sourceUrls },
  { label: 'Trailing revenue', value: metaTtm.revenueBn, unit: 'USD billions', period: metaTtm.period, basis: 'FY2025 200.966 + H1FY2026 117.111 − H1FY2025 89.830.', sourceUrls: reportedFlowSources },
  { label: 'Trailing operating income', value: metaTtm.operatingIncomeBn, unit: 'USD billions', period: metaTtm.period, basis: 'FY2025 83.276 + H1FY2026 41.647 − H1FY2025 37.997.', sourceUrls: reportedFlowSources },
  { label: 'Trailing depreciation and amortization', value: metaTtm.depreciationAmortizationBn, unit: 'USD billions', period: metaTtm.period, basis: 'Cash-flow statement: FY2025 18.616 + H1FY2026 12.355 − H1FY2025 8.242.', sourceUrls: reportedFlowSources },
  { label: 'Calculated trailing EBITDA', value: metaTtm.ebitdaBn, unit: 'USD billions', period: metaTtm.period, basis: metaTtm.ebitdaBasis, sourceUrls: reportedFlowSources },
  { label: 'Common shares outstanding', value: metaMarket.sharesBn, unit: 'billion shares', period: metaMarket.sharesDate, basis: '2,205,128,509 Class A + 342,377,716 Class B. Actual cover-page count; held fixed in scenarios.', sourceUrls: [metaValuationSources.filing] },
  { label: 'Diluted weighted-average shares', value: 2.566, unit: 'billion shares', period: 'Q2 FY2026', basis: 'Used in reported quarterly EPS; not substituted for the common shares outstanding in market capitalization.', sourceUrls: [metaValuationSources.q2] },
  { label: 'Cash plus marketable securities', value: 90.260, unit: 'USD billions', period: metaReported.balance.date, basis: 'Cash and equivalents 15.462 + marketable securities 74.798; excludes restricted cash and nonmarketable investments.', sourceUrls: [metaValuationSources.q2] },
  { label: 'Long-term debt', value: 83.664, unit: 'USD billions', period: metaReported.balance.date, basis: 'Reported balance-sheet carrying value; not presented as independently verified total enterprise claims.', sourceUrls: [metaValuationSources.q2] },
  { label: 'Operating lease liabilities', value: 28.654, unit: 'USD billions', period: metaReported.balance.date, basis: 'Current 2.425 + noncurrent 26.229.', sourceUrls: [metaValuationSources.q2] },
  { label: 'Provider market capitalization', value: metaMarket.marketCapBn, unit: 'USD billions', period: metaMarket.date, basis: 'Stock Analysis displayed value; provider rounding differs slightly from close × filing shares.', sourceUrls: [metaValuationSources.ratios] },
  { label: 'Provider enterprise value', value: metaMarket.enterpriseValueBn, unit: 'USD billions', period: metaMarket.date, basis: 'Stock Analysis definition; not independently reconstructed with assumed-zero claims.', sourceUrls: [metaValuationSources.ratios] },
  { label: 'Provider net claims', value: metaEnterpriseBridge.netClaimsBn, unit: 'USD billions', period: metaMarket.date, basis: 'Enterprise value 1936.917 − market capitalization 1914.859; aggregate held fixed in the EV sensitivity.', sourceUrls: [metaValuationSources.ratios] },
  { label: 'Book equity', value: 261.221, unit: 'USD billions', period: metaReported.balance.date, basis: 'Reported total stockholders’ equity; accounting book value, not NAV.', sourceUrls: [metaValuationSources.q2] },
]

// A small attributed annual excerpt, not a fabricated continuous trading series.
// Source fiscal-year labels refer to year-end snapshots, including non-trading dates.
export const metaHistoricalAnnualPoints = [
  { date: '2021-12-31', 'P/E': 23.77, 'P/S': 7.93, 'EV/EBITDA': 16.28, 'P/book': 7.49 },
  { date: '2022-12-31', 'P/E': 13.60, 'P/S': 2.71, 'EV/EBITDA': 7.11, 'P/book': 2.51 },
  { date: '2023-12-31', 'P/E': 23.28, 'P/S': 6.75, 'EV/EBITDA': 15.29, 'P/book': 5.94 },
  { date: '2024-12-31', 'P/E': 23.70, 'P/S': 8.99, 'EV/EBITDA': 17.17, 'P/book': 8.09 },
  { date: '2025-12-31', 'P/E': 27.52, 'P/S': 8.28, 'EV/EBITDA': 16.39, 'P/book': 7.66 },
] as const

export function metaHistoricalContext(): ValuationResearchContext {
  const metrics = ['P/E', 'P/S', 'EV/EBITDA', 'P/book'] as const
  return {
    as_of: META_VALUATION_AS_OF,
    historical_multiples: Object.fromEntries(metrics.map(metric => {
      const values = metaHistoricalAnnualPoints.map(point => point[metric])
      return [metric, {
        metric, provider: 'Stock Analysis', provenance: 'secondary_provider', sampling: 'annual', as_of: META_VALUATION_AS_OF,
        currency: 'USD', source_url: metaValuationSources.ratios, min: Math.min(...values), max: Math.max(...values),
        basis: 'Five actual fiscal-year-end provider snapshots, FY2021–FY2025. Not a monthly or daily history. Fiscal period labels may fall on non-trading dates.',
        coverage_note: 'Source: Stock Analysis annual META ratios (data provider: S&P Global Market Intelligence), checked September 26, 2026. These provider-computed ratios can use restated financials and need not match calculations using information available on each historical date. The average and standard-deviation bands summarize these five annual samples only; they are not backtest-ready or forecast confidence intervals. No missing months are invented. P/book is distinct from NAV.',
        points: metaHistoricalAnnualPoints.map(point => ({ date: point.date, period_label: `FY${point.date.slice(0, 4)} year-end`, multiple: point[metric], source_url: metaValuationSources.ratios, source_refs: [metaValuationSources.ratios] })),
      }]
    })),
  }
}

export const metaScenarios = [
  { label: 'Bear', growthPercent: -10, multiple: 20, rationale: 'Earnings shrink and investors pay less for each dollar of profit.' },
  { label: 'Base', growthPercent: 8, multiple: 26, rationale: 'Modest trailing-EPS growth with a multiple below the current market P/E; an analysis assumption, not consensus.' },
  { label: 'Bull', growthPercent: 20, multiple: 32, rationale: 'Faster earnings growth plus a premium multiple; both assumptions must work.' },
].map(scenario => {
  const result = calculateMetaPrice('P/E', scenario.growthPercent, scenario.multiple)
  if (result.today === null || result.future === null || result.upsidePercent === null) throw new Error(`Invalid META ${scenario.label} scenario: ${result.reason}`)
  return { ...scenario, today: result.today, future: result.future, upsidePercent: result.upsidePercent, horizonMonths: 12, basis: 'Reported trailing GAAP EPS, unadjusted for unusual tax items' }
})

export const metaEntry = {
  price: round(metaScenarios[1].today * 0.8, 2),
  marginOfSafetyPercent: 20,
  assumption: 'Research watchlist rule: 20% below the base case’s implied price today (reported trailing EPS × 26). A fixed published research assumption, not a market quote or a private saved decision. Changing the sensitivity controls does not change this saved threshold.',
}
