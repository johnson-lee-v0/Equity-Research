const PRIVATE_VALUE_KEYS = new Set([
  'account_balance',
  'account_value',
  'available_cash',
  'available_funds',
  'buying_power',
  'cash_balance',
  'cost_basis',
  'current_value',
  'executed_notional',
  'filled_notional',
  'funded_amount',
  'funded_value',
  'held_shares',
  'market_value',
  'net_worth',
  'owned_quantity',
  'owned_shares',
  'personal_allocation',
  'portfolio_value',
  'position_value',
  'quantity_held',
  'shares_held',
])

const PRIVATE_CONTAINER_KEYS = new Set([
  'account',
  'accounts',
  'account_context',
  'account_inputs',
  'account_snapshot',
  'allocation',
  'allocation_inputs',
  'balances',
  'cash_account',
  'funding',
  'holdings',
  'holdings_snapshot',
  'personal_portfolio',
  'portfolio',
  'portfolio_context',
  'portfolio_inputs',
  'portfolio_snapshot',
  'position',
  'positions',
  'position_snapshot',
  'sizing_inputs',
  'transactions',
])

function normalizedKey(value: string) {
  return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
}

/** Fields that describe supplied account or personal allocation values. */
export function isPrivateFinancialKey(key: string) {
  const normalized = normalizedKey(key)
  if (!normalized) return false
  if (PRIVATE_VALUE_KEYS.has(normalized) || PRIVATE_CONTAINER_KEYS.has(normalized)) return true
  if (/^(?:account|portfolio|position|holding|balance|cash|funding|allocation|transaction)(?:_|$)/.test(normalized)) return true
  if (/(?:_|^)(?:notional|quantity|shares|target_weight|position_weight|allocation_percent)(?:_|$)/.test(normalized)) return true
  return false
}

export function isPrivateFinancialContainerKey(key: string) {
  return PRIVATE_CONTAINER_KEYS.has(normalizedKey(key))
}

/**
 * Finds private values in structured API data without treating every number as
 * private. A generic notional or quantity becomes private only when it is
 * nested in an account/portfolio container; candidate cards can still label
 * their own proposed sizing explicitly.
 */
export function hasPrivateFinancialData(value: unknown, privateScope = false): boolean {
  if (Array.isArray(value)) return value.some((item) => hasPrivateFinancialData(item, privateScope))
  if (!value || typeof value !== 'object') return false
  return Object.entries(value as Record<string, unknown>).some(([key, child]) => {
    const normalized = normalizedKey(key)
    if (PRIVATE_VALUE_KEYS.has(normalized)) return true
    const childScope = privateScope || isPrivateFinancialContainerKey(key)
    if (childScope && isPrivateFinancialKey(key)) return true
    return hasPrivateFinancialData(child, childScope)
  })
}

/** Detect likely personal account amounts in free-form retained text. */
export function hasPrivateFinancialText(value: unknown) {
  if (typeof value !== 'string' || !value.trim()) return false
  const amount = '(?:[$€£]\\s*[\\d,]+(?:\\.\\d{2})?|(?:USD|CAD|EUR|GBP)\\s*[\\d,]+(?:\\.\\d{2})?|\\b\\d[\\d,]*\\.\\d{2}\\b)'
  const genericAccountAmount = new RegExp(`\\b(?:account\\s+(?:balance|value|cash)|available\\s+(?:cash|funds)|cash\\s+balance|portfolio\\s+(?:value|balance)|net\\s+worth|buying\\s+power|(?:held|owned)\\s+(?:shares|position)|position\\s+(?:value|cost\\s+basis)|personal\\s+(?:allocation|portfolio))\\b[\\s\\S]{0,100}${amount}`, 'i')
  const namedAccountAmount = new RegExp(`\\b(?:tfsa|rrsp|rrif|non[-\\s]?registered|chequing|checking)\\b[\\s\\S]{0,120}\\b(?:balance|cash|value|holding|position|account|funds?|available|contribution|deposit)\\b[\\s\\S]{0,120}${amount}|${amount}[\\s\\S]{0,120}\\b(?:tfsa|rrsp|rrif|non[-\\s]?registered|chequing|checking)\\b[\\s\\S]{0,80}\\b(?:balance|cash|value|holding|position|account|funds?|available|contribution|deposit)\\b`, 'i')
  const policyAmount = new RegExp(`\\b(?:account|portfolio|cash|budget|loss|risk(?:\\s+(?:budget|limit|cap))?|allocation|headroom|exposure|weight|notional|shares?|margin|borrow|buying\\s+power|cost\\s+basis|position\\s+(?:size|value|budget|limit))\\b[\\s\\S]{0,100}${amount}|${amount}[\\s\\S]{0,100}\\b(?:account|portfolio|cash|budget|loss|risk(?:\\s+(?:budget|limit|cap))?|allocation|headroom|exposure|weight|notional|shares?|margin|borrow|buying\\s+power|cost\\s+basis|position\\s+(?:size|value|budget|limit))\\b`, 'i')
  const policyQuantity = /\b(?:proposed|recommended|maximum|planned|approved|configured|policy|risk|position|allocation|owned|held)\b[\s\S]{0,80}\b\d[\d,]*\s+shares?\b|\b\d[\d,]*\s+shares?\b[\s\S]{0,80}\b(?:proposed|recommended|maximum|planned|approved|configured|policy|risk|position|allocation|owned|held)\b/i
  const accountIdentifier = /\b(?:account|portfolio|position|holding)\s*(?:id|identifier|#)\s*[:=]?\s*[A-Za-z0-9_-]{2,}\b/i
  return genericAccountAmount.test(value) || namedAccountAmount.test(value) || policyAmount.test(value) || policyQuantity.test(value) || accountIdentifier.test(value)
}

/** Source metadata is a stronger signal than ordinary research prose. */
export function isPersonalFinancialSource(value: unknown) {
  if (!value || typeof value !== 'object') return false
  const source = value as Record<string, unknown>
  const metadata = [source.source_type, source.kind, source.import_kind, source.title].filter((item) => typeof item === 'string').join(' ').toLowerCase()
  return /(?:account\s+statement|balance\s+observation|personal\s+(?:account|portfolio)|(?:portfolio|holdings?)\s+(?:snapshot|observation|ledger)|brokerage|tfsa|rrsp|rrif|non[-\s]?registered|chequing|checking|\bbalances?\b|\btransactions?\b)/i.test(metadata)
    || hasPrivateFinancialData(source.accounts)
    || hasPrivateFinancialData(source.balances)
    || hasPrivateFinancialData(source.positions)
}
