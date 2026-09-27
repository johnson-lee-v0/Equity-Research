import type { ReactNode } from 'react'

export function ResearchHeader({
  title,
  children,
  action,
}: {
  eyebrow: string
  title: string
  children: ReactNode
  action?: ReactNode
}) {
  return (
    <header className="workspace-header">
      <div>
        <h1>{title}</h1>
        <p>{children}</p>
      </div>
      {action}
    </header>
  )
}

export function ErrorNotice({ message }: { message: string }) {
  return message ? (
    <p className="research-error" role="alert">
      {message}
    </p>
  ) : null
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <p className="research-empty">{children}</p>
}

export function Metric({
  label,
  value,
  detail,
}: {
  label: string
  value: ReactNode
  detail?: string
}) {
  return (
    <div className="research-metric">
      <span>{label}</span>
      <strong>{value}</strong>
      {detail && <small>{detail}</small>}
    </div>
  )
}

export function displayDate(value?: string | null) {
  if (!value) return 'Unknown'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        timeZone: 'UTC',
      })
}

export function formatNumber(value?: number | null, digits = 0) {
  return value == null || !Number.isFinite(value)
    ? '—'
    : value.toLocaleString(undefined, { maximumFractionDigits: digits })
}

export function percent(value?: number | null) {
  return value == null || !Number.isFinite(value) ? '—' : `${(value * 100).toFixed(1)}%`
}

export function sourceUrl(value?: string | null) {
  if (!value) return undefined
  try {
    const url = new URL(value, window.location.origin)
    return ['https:', 'http:'].includes(url.protocol) ? url.href : undefined
  } catch {
    return undefined
  }
}

export function downloadJson(value: unknown, filename: string) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }),
  )
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}
