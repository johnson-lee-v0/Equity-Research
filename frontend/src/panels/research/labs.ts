import { apiFetch } from '../../api'

export type LabCatalog = {
  canImport?: boolean
  owners: string[]
  methodNotes: string[]
  available: boolean
  metadata: Record<string, unknown>
  politicians: { id: string; name: string; chamber: string; records: number }[]
  strategies: { id: string; label: string; description: string }[]
  research: { id: string; label: string; available: boolean }[]
  watch: Record<string, unknown>
}

export type Disclosure = {
  id: string
  politicianId: string
  politician: string
  chamber: string
  owner: string
  account: string
  ticker: string
  asset: string
  priceSymbol: string
  tradeDate: string
  filedDate: string
  action: string
  amountRange: string
  eligible: boolean
  exclusion: string
  source: string
}

export type BacktestRequest = {
  strategy: string
  politician: string
  chamber: string
  start: string
  end: string
  sizing: string
  delay: number
  short_holding: boolean
  add_purchases: boolean
  exit_price: string
  fee_bps: number
}
export type Backtest = {
  id: string
  created_at: string
  request: BacktestRequest
  metrics: {
    endingValue: number
    totalReturn: number
    cagr: number
    maxDrawdown: number
    volatility: number
    sharpe: number
    benchmarkReturn: number
  }
  curve: { date: string; nav: number; benchmark: number; cash: number; invested: number }[]
  entries: Record<string, unknown>[]
  holdings: Record<string, unknown>[]
  closed: Record<string, unknown>[]
  orders: Record<string, unknown>[]
  exclusions: Record<string, unknown> | Record<string, unknown>[]
  assumptions: unknown
}

export function getCatalog(signal?: AbortSignal) {
  return apiFetch<LabCatalog>('/api/labs/catalog', {}, { signal })
}

export function textValue(value: unknown): string {
  if (value == null) return '—'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

export function humanize(value: string) {
  return value.replace(/([a-z])([A-Z])/g, '$1 $2').replaceAll('_', ' ')
}
