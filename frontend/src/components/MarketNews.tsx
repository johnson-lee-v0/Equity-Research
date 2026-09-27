import { useEffect, useState } from 'react'
import { apiFetch } from '../api'
import './market-news.css'

export type MarketNewsItem = { id: string; title: string; url: string; source: string; published_at: string }
export type MarketNewsSnapshot = { status: 'fresh' | 'stale' | 'unavailable'; items: MarketNewsItem[]; fetched_at: string | null; message?: string | null; feeds?: Array<{ name: string; url: string }> }

function safeNewsUrl(value: string) {
  try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password ? url.href : null } catch { return null }
}
function newsTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? 'Time unavailable' : new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(date)
}

export function MarketNewsShelf({ snapshot, loading = false, onRetry }: { snapshot: MarketNewsSnapshot | null; loading?: boolean; onRetry?: () => void }) {
  const checkedAt = snapshot?.fetched_at ? Date.parse(snapshot.fetched_at) : NaN
  const snapshotAge = Number.isFinite(checkedAt) ? Date.now() - checkedAt : 0
  const status = snapshotAge > 86_400_000 && snapshot?.status === 'fresh' ? 'stale' : snapshot?.status
  const items = (snapshotAge > 7 * 86_400_000 ? [] : snapshot?.items ?? []).filter((item) => safeNewsUrl(item.url)).slice(0, 6)
  return <section className="market-news" aria-label="Market news" aria-busy={loading}>
    <header><h2>Market news</h2><span>{status === 'stale' ? 'Saved headlines' : 'Latest headlines'}{snapshot?.fetched_at && <> · Checked <time dateTime={snapshot.fetched_at}>{newsTime(snapshot.fetched_at)}</time></>}</span></header>
    {loading && !snapshot ? <p role="status" className="market-news-status">Loading headlines…</p> : items.length ? <div className="market-news-grid">{items.map((item) => <a className="market-news-tile" href={safeNewsUrl(item.url)!} target="_blank" rel="noopener noreferrer" key={item.id}>
      <span className="market-news-source">{item.source}<span aria-hidden="true">↗</span></span>
      <h3>{item.title}</h3><time dateTime={item.published_at}>{newsTime(item.published_at)}</time><span className="sr-only">Opens original article in a new tab</span>
    </a>)}</div> : <div className="market-news-status" role="status"><span>Market headlines are temporarily unavailable.</span>{onRetry && <button type="button" className="button" onClick={onRetry} disabled={loading}>Try again</button>}</div>}
    {status === 'fresh' && snapshot?.message && items.length > 0 && items.length < 6 && <p className="market-news-status">{snapshot.message}</p>}
    {status === 'stale' && items.length > 0 && <p className="market-news-status">The feed could not refresh. These are the last saved headlines.</p>}
  </section>
}

export default function MarketNews({ snapshotUrl = '/api/market-news' }: { snapshotUrl?: string } = {}) {
  const [snapshot, setSnapshot] = useState<MarketNewsSnapshot | null>(null)
  const [loading, setLoading] = useState(true)
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    apiFetch<MarketNewsSnapshot>(snapshotUrl, {}, { signal: controller.signal })
      .then((data) => { if (!controller.signal.aborted) setSnapshot(data) })
      .catch(() => { if (!controller.signal.aborted) setSnapshot((old) => old?.items.length ? { ...old, status: 'stale' } : { status: 'unavailable', items: [], fetched_at: null }) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    // Refresh only the public RSS cache, never a research case or model loop.
    const timer = window.setInterval(() => setRevision((value) => value + 1), 600_000)
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [revision, snapshotUrl])
  return <MarketNewsShelf snapshot={snapshot} loading={loading} onRetry={() => setRevision((value) => value + 1)} />
}
