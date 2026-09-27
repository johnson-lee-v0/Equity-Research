import type { EvidenceRef } from '../types'

type EventReview = { ticker?: string; status?: string; window_start?: string; cutoff?: string; checked_at?: string; baseline_filing?: {form?: string; period_end?: string; filed_at?: string} | null; events?: {source_id: string; title: string; published_at?: string; event_date?: string; kind?: string; quote?: string}[]; checks?: {url?: string; status?: string; reason?: string}[]; gaps?: string[]; coverage?: string }
export default function InterimEvents({value, ticker, onOpenSource}: {value: unknown; ticker?: string; onOpenSource: (source: EvidenceRef) => void | Promise<void>}) {
  const reviews = (Array.isArray(value) ? value : []).filter((item): item is EventReview => !!item && typeof item === 'object' && (!ticker || String(item.ticker).toUpperCase() === ticker.toUpperCase()))
  return <section className="interim-events"><h2>Recent company announcements</h2><p>Company announcements, press releases and events checked through this review’s research cutoff.</p>
    {!reviews.length && <p className="muted-copy">No announcement check is saved for this review yet. New research includes this step.</p>}
    {reviews.map((review, i) => <div key={i}>
      <p className="research-action-meta">Coverage window: {review.window_start || 'Start date unavailable'} → {review.cutoff || 'Cutoff unavailable'} · {review.status || 'Not recorded'}</p>
      {review.baseline_filing && <p className="research-action-meta">Latest periodic filing: {review.baseline_filing.form || 'Form unavailable'} · Period ended {review.baseline_filing.period_end || 'date unavailable'} · Filed {review.baseline_filing.filed_at || 'date unavailable'}</p>}
      {review.events?.map((event) => <article key={event.source_id}><strong>{event.title}</strong><p className="research-action-meta">Published {event.published_at || 'date unavailable'}{event.event_date ? ` · Event ${event.event_date}` : ''}</p>{event.quote && <details><summary>Read the source words</summary><blockquote>{event.quote}</blockquote></details>}<button type="button" className="button" onClick={() => void onOpenSource({id:event.source_id,title:event.title} as EvidenceRef)}>Open source ↗</button></article>)}
      {(review.gaps?.length || review.checks?.length) ? <details><summary>Coverage gaps and source checks</summary>{!!review.gaps?.length && <ul>{review.gaps.map((gap,j) => <li key={j}>{gap}</li>)}</ul>}{!!review.checks?.length && <ul>{review.checks.map((check,j) => <li key={j}>{check.reason || check.status || 'Source unavailable'}{check.url && <span> — {check.url}</span>}</li>)}</ul>}</details> : null}
      <p className="research-action-meta">{review.coverage}</p>
    </div>)}
  </section>
}
