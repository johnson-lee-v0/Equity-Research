// Research interpretation and financial context are separate from call paraphrases.
const release = 'https://investor.atmeta.com/investor-news/press-release-details/2026/Meta-Reports-Second-Quarter-2026-Results/'
const launch = 'https://about.fb.com/news/2026/09/introducing-muse-personal-ai-agent/'
export type MetaThemeContext = {
  id: string; title: string; takeaway: string; metricIds: string[]; metricNote: string
  facts?: string[]; sourceUrl?: string; sourceLabel?: string; openQuestion?: string
}
export const metaThemeContexts: MetaThemeContext[] = [
  { id: 'growth', title: 'Growth & advertising', takeaway: 'Check whether a growing advertising business also earns more profit.',
    facts: ['Q2 ad impressions +14%; average ad price +12%; daily active people 3.60bn.'], sourceUrl: release, sourceLabel: 'Reported operating metrics',
    metricIds: ['revenue_yoy_growth', 'revenue', 'operating_margin'], metricNote: 'Company-wide figures provide context; they do not measure the isolated effect of a recommendation change.' },
  { id: 'profitability', title: 'Profitability & costs', takeaway: 'Separate unusual costs from spending that will keep recurring.',
    facts: ['Q2 expenses $42.026bn, including $2.40bn legal charges and $1.18bn severance.'], sourceUrl: release, sourceLabel: 'Reported costs',
    openQuestion: 'How much margin recovers after unusual charges, while infrastructure costs continue?', metricIds: ['operating_margin', 'diluted_eps'], metricNote: 'EPS also reflects unusual tax items. The valuation section identifies them.' },
  { id: 'monetization', title: 'AI businesses', takeaway: 'Compare the route to paying customers with the money needed to build it.',
    metricIds: ['revenue', 'operating_margin'], metricNote: 'These are total-company results. No separate API or business-agent revenue series was retained.' },
  { id: 'consumer', title: 'Consumer agents', takeaway: 'The July product discussion and September Muse release are different evidence dates.',
    facts: ['Muse launched September 8; standalone revenue and paid retention remain undisclosed in the launch.'], sourceUrl: launch, sourceLabel: 'Later Muse announcement',
    metricIds: [], metricNote: 'No sourced historical paid-user, retention or profit series for Muse is available. Company revenue is not a substitute.' },
  { id: 'models', title: 'Models & competition', takeaway: 'A stronger model needs a business advantage that survives its serving costs.',
    metricIds: [], metricNote: 'Model rankings do not supply unit economics. No comparable serving-cost history was collected.' },
  { id: 'funding', title: 'Spending & funding', takeaway: 'Track investment commitments alongside cash generation and financing needs.',
    facts: ['June 30 cash and marketable securities $90.260bn; long-term debt $83.664bn.'], sourceUrl: release, sourceLabel: 'Reported balance sheet',
    metricIds: ['capex_quarterly', 'capex', 'operating_cash_flow'], metricNote: 'CapEx includes finance-lease principal. Annual guidance comparisons use the same management measure.' },
  { id: 'capacity', title: 'Capacity & cash', takeaway: 'Construction only helps shareholders if useful capacity eventually generates cash returns.',
    metricIds: ['free_cash_flow', 'operating_cash_flow', 'capex_quarterly'], metricNote: 'Cash-flow history shows the funding pressure; it does not establish returns from an individual facility.' },
  { id: 'outlook', title: 'Outlook & risks', takeaway: 'Keep management forecasts distinct from reported spending and completed results.',
    facts: ['Q3 revenue guidance $61–64bn; FY2026 expenses $165–169bn; CapEx $130–145bn.', 'The company flags potential additional losses from youth-related litigation.'], sourceUrl: release, sourceLabel: 'Management outlook and risks',
    openQuestion: 'Can profit growth absorb the investment bill and further legal costs?', metricIds: ['capex', 'free_cash_flow'], metricNote: 'Future guidance is not an actual. A historical forecast miss alone does not establish its cause.' },
]
