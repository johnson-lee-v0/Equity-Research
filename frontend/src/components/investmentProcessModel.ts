export type EarningsReviewReceipt = {
  ticker?: string
  status?: string
  workflow_id?: string
  fiscal_period?: string
  period_end?: string
  gaps?: string[]
}

const object = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}

/** A display name such as “Costco common stock” is not a ticker identity. */
export function selectedResearchTicker(value: unknown, candidate: unknown, candidateCount: number): string | undefined {
  const run = object(value), item = object(candidate)
  const values = [item.ticker, item.symbol, candidateCount === 1 ? run.ticker : undefined]
  return values.find((value): value is string => typeof value === 'string' && /^[A-Z0-9][A-Z0-9.-]{0,14}$/i.test(value))?.toUpperCase()
}

/** A saved assessment must keep its own earnings review, even after a newer refresh. */
export function earningsReviewForRun(value: unknown, ticker?: string): EarningsReviewReceipt | undefined {
  const run = object(value)
  const process = object(run.investment_process)
  const receipts = Array.isArray(process.earnings) ? process.earnings.map(object) : []
  const receipt = ticker ? receipts.find((item) => String(item.ticker ?? '').toUpperCase() === ticker.toUpperCase()) : receipts.length === 1 ? receipts[0] : undefined
  if (receipt) return receipt as EarningsReviewReceipt
  const origin = typeof run.origin_ref === 'string' ? run.origin_ref : ''
  if (origin.startsWith('workflow:') && (!ticker || !run.ticker || String(run.ticker).toUpperCase() === ticker.toUpperCase())) {
    return { workflow_id: origin.slice('workflow:'.length), ticker, status: 'saved' }
  }
  return undefined
}

export function earningsReviewHref(workflowId: string): string {
  return `?earnings=${encodeURIComponent(workflowId)}#research/earnings`
}

export function earningsReviewLinkLabel(review?: EarningsReviewReceipt, latest = false): string {
  const saved = ['completed', 'partial', 'saved'].includes(review?.status || '')
  return latest
    ? saved ? 'Open latest saved earnings review' : 'View latest earnings collection'
    : saved ? 'Open earnings review used in this assessment' : 'View earnings collection'
}

export function initialEarningsSelection(search: string, stored: string | null): string {
  return new URLSearchParams(search).get('earnings')?.trim() || stored || ''
}

export function replacementEarningsSelection<T extends { id: string }>(items: T[], missingId: string): T | undefined {
  return items.find((item) => item.id !== missingId)
}

/** Offer an explicit source revision without replacing the assessment's evidence. */
export function newerSourceCoverage<T extends { id: string; ticker: string; status: string; created_at: string; source_refresh_of?: string | null }>(items: T[], selectedId: string, ticker: string): T | undefined {
  const family = new Set([selectedId])
  for (let pass = 0; pass < items.length; pass++) {
    const before = family.size
    for (const item of items) {
      if (item.ticker === ticker && item.source_refresh_of && family.has(item.source_refresh_of)) family.add(item.id)
    }
    if (family.size === before) break
  }
  return items.filter((item) => item.id !== selectedId && family.has(item.id) && ['completed', 'partial'].includes(item.status))
    .sort((a, b) => b.created_at.localeCompare(a.created_at))[0]
}

export function earningsStageLabel(review?: EarningsReviewReceipt): string {
  if (!review) return 'Not yet reviewed'
  if (review.status === 'collecting' || review.status === 'running' || review.status === 'queued') return 'Collecting materials'
  if (review.status === 'partial') return 'Saved · gaps remain'
  if (review.status === 'completed' || review.status === 'saved') return 'Saved review'
  if (review.status === 'not_applicable') return 'Not applicable'
  if (review.status === 'unavailable' || review.status === 'failed') return 'Evidence unavailable'
  return review.workflow_id ? 'Saved review' : 'Not yet reviewed'
}
