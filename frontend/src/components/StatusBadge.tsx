import type { TaskStatus } from '../types'

const LABELS: Record<string, string> = {
  idle: 'Idle', queued: 'Queued', running: 'Running', waiting_for_evidence: 'Waiting for evidence', waiting_for_review: 'Waiting for review', awaiting_input: 'Awaiting information', blocked: 'Blocked', failed: 'Failed', cancelled: 'Cancelled', completed: 'Completed', interrupted: 'Interrupted', paused: 'Paused', ready: 'Ready', auth_required: 'Authentication required', missing: 'Not installed', unavailable: 'Unavailable', unknown: 'Unknown',
}

export function statusLabel(status?: string | null) {
  if (!status) return LABELS.unknown
  return LABELS[status] ?? status.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

export function StatusBadge({ status, paused = false, compact = false }: { status?: TaskStatus | string | null; paused?: boolean; compact?: boolean }) {
  const value = paused ? 'paused' : status ?? 'unknown'
  return <span className={`status-badge status-${value}${compact ? ' status-compact' : ''}`}><span className="status-dot" aria-hidden="true" />{statusLabel(value)}</span>
}
