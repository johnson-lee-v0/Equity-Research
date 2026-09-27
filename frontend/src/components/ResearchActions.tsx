import { useEffect, useRef, useState } from 'react'
import { apiFetch } from '../api'
import type { EvidenceRef, Namespace } from '../types'

type Bullet = { evidence_status?: string; claim?: string; summary?: string; source_ids?: string[]; quote?: string; quotes?: { source_id: string; quote: string }[] }
type Action = { run_id: string; kind: 'challenge' | 'evidence_retry'; status: string; created_at: string; scope: { gap_id?: string; source_id?: string; description?: string }; result?: { bull_case?: (Bullet | string)[]; bear_case?: (Bullet | string)[]; resolution?: string | Bullet; open_questions?: (Bullet | string)[]; what_would_change_mind?: (Bullet | string)[]; findings?: (Bullet | string)[]; summary?: string; remaining_gap?: string; source_ids?: string[]; status?: string }; error?: string }
type Snapshot = { items: Action[]; eligibility: { challenge: boolean; evidence_retry?: boolean; reason?: string }; firm_paused: boolean; gaps?: { id?: string; gap_id?: string; description?: string; question?: string; reason?: string; status?: string; retry_eligible?: boolean; terminal_reason?: string }[]; sources?: { id?: string; source_id?: string; title?: string; retry_eligible?: boolean }[] }
const active = (status: string) => ['queued', 'running', 'paused', 'pending', 'waiting_for_evidence', 'waiting_for_review', 'waiting_evidence', 'waiting_review'].includes(status)

export default function ResearchActions({ runId, namespace, parentStatus, onOpenSource }: { runId: string; namespace: Namespace; parentStatus: string | null | undefined; onOpenSource: (source: EvidenceRef) => void | Promise<void> }) {
  const [data, setData] = useState<Snapshot | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const [refresh, setRefresh] = useState(0)
  const requestKeys = useRef<Record<string, string>>({})
  const identity = useRef({ namespace, runId })
  if (identity.current.namespace !== namespace || identity.current.runId !== runId) identity.current = { namespace, runId }
  useEffect(() => {
    setBusy('')
    requestKeys.current = {}
  }, [runId, namespace])
  useEffect(() => {
    const controller = new AbortController()
    let timer: number | undefined
    setData(null); setError('')
    async function load() {
      try {
        const next = await apiFetch<Snapshot>(`/api/runs/${encodeURIComponent(runId)}/research-actions?namespace=${namespace}`, {}, { signal: controller.signal })
        if (controller.signal.aborted) return
        setData(next)
        if (next.items.some((item) => active(item.status))) timer = window.setTimeout(() => void load(), 2500)
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Optional research is unavailable.')
      }
    }
    void load()
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [runId, namespace, parentStatus, refresh])
  async function launch(kind: Action['kind'], scope: { gap_id?: string; source_id?: string } = {}) {
    const key = `${kind}:${scope.gap_id || scope.source_id || 'case'}`
    const captured = identity.current
    if (busy) return
    setBusy(key); setError('')
    requestKeys.current[key] ||= crypto.randomUUID()
    try {
      await apiFetch(`/api/runs/${encodeURIComponent(runId)}/research-actions`, { method: 'POST', body: JSON.stringify({ namespace, kind, idempotency_key: requestKeys.current[key], ...scope }) }, { mutation: true })
      if (captured === identity.current) { delete requestKeys.current[key]; setRefresh((value) => value + 1) }
    } catch (cause) { if (captured === identity.current) setError(cause instanceof Error ? cause.message : 'The request could not be saved.') }
    finally { if (captured === identity.current) setBusy('') }
  }
  async function control(item: Action, action: 'run_once' | 'cancel') {
    const captured = identity.current
    if (busy) return
    setBusy(item.run_id); setError('')
    try {
      await apiFetch('/api/control', {method:'POST', body:JSON.stringify({scope:'run',id:item.run_id,action})}, {mutation:true})
      if (captured === identity.current) setRefresh((value) => value + 1)
    } catch(cause) { if (captured === identity.current) setError(cause instanceof Error ? cause.message : 'Could not update this review.') }
    finally {if (captured === identity.current) setBusy('')}
  }
  function references(ids: string[] = []) { return ids.map((id) => <button key={id} type="button" className="transcript-text-button" onClick={() => void onOpenSource({id, title: data?.sources?.find((source) => (source.id || source.source_id) === id)?.title || 'Supporting source'} as EvidenceRef)}>View source ↗</button>) }
  function bullets(values: (Bullet | string)[] = []) {
    if (!values.length) return <p>No supported points were retained.</p>
    return <ul>{values.map((value, index) => { const bullet = typeof value === 'string' ? {claim: value} : value
      return <li key={index}>{bullet.claim || bullet.summary}{bullet.evidence_status && bullet.evidence_status !== 'supported' && <small className="research-action-meta"> · {bullet.evidence_status}</small>}{(bullet.quote || bullet.quotes?.length || bullet.source_ids?.length) ? <details><summary>Supporting evidence</summary>{bullet.quote && <blockquote>{bullet.quote}</blockquote>}{bullet.quotes?.map((quote, i) => <blockquote key={i}>{quote.quote}{references([quote.source_id])}</blockquote>)}{references(bullet.source_ids)}</details> : null}</li>
    })}</ul>
  }
  const challenge = data?.items.find((item) => item.kind === 'challenge' && active(item.status))
  const challenging = Boolean(challenge)
  const challengeWaiting = challenge && ['queued', 'paused', 'pending'].includes(challenge.status)
  return <section className="research-actions" aria-label="Optional deeper research"><h2>Want to look closer?</h2><p>The initial review stays saved. These optional checks add a separate result.</p>
    <button type="button" className="button button-primary" disabled={!!busy || !data?.eligibility.challenge || challenging} onClick={() => void launch('challenge')}>{challengeWaiting ? 'Challenge waiting to run' : challenging ? 'Challenge in progress' : busy === 'challenge:case' ? 'Saving challenge…' : 'Challenge this ticker'}</button>
    <p className="research-action-meta">A case for the company, a case against it, and a review of where they disagree.</p>
    {data?.firm_paused && <p>Background research is paused. “Run this review now” starts only the selected review and keeps background research paused.</p>}
    {!data?.eligibility.challenge && data?.eligibility.reason && <p>{data.eligibility.reason}</p>}
    {error && <p role="alert">{error}</p>}
    {!!data?.gaps?.length && <div><h3>Look again at one missing piece</h3>{data.gaps.map((gap, index) => {const id = gap.gap_id || gap.id; const pending = data.items.some((item) => item.kind === 'evidence_retry' && item.scope.gap_id === id && active(item.status)); return <div className="research-action-gap" key={id || index}><p>{gap.description || gap.question || gap.reason || 'Unresolved evidence'}</p><button type="button" className="button" disabled={!id || !!busy || pending || data.eligibility.evidence_retry === false || gap.retry_eligible === false} onClick={() => void launch('evidence_retry', {gap_id:id})}>{pending ? 'Checking this evidence…' : gap.retry_eligible === false ? 'Needs separate input' : 'Retry this evidence'}</button></div>})}</div>}
    {!!data?.sources?.length && <details className="decision-details"><summary>Recheck a particular source</summary>{data.sources.map((source, index) => {const id = source.source_id || source.id; const pending = data.items.some((item) => item.kind === 'evidence_retry' && item.scope.source_id === id && active(item.status)); return <div className="research-action-gap" key={id || index}><p>{source.title || id}</p><button className="button" type="button" disabled={!id || !!busy || pending || data.eligibility.evidence_retry === false || source.retry_eligible === false} onClick={() => void launch('evidence_retry', {source_id:id})}>{pending ? 'Checking this source…' : source.retry_eligible === false ? 'No public source to retry' : 'Retry this evidence'}</button></div>})}</details>}
    {data?.items.map((item) => <article className="research-action-record" key={item.run_id}><h3>{item.kind === 'challenge' ? 'Challenge review' : 'Evidence follow-up'}</h3><p className="research-action-meta">{item.status.replaceAll('_',' ')} · {new Date(item.created_at).toLocaleString()}{item.scope.description ? ` · ${item.scope.description}` : ''}</p>
      {['queued','paused','pending'].includes(item.status) && <button className="button" disabled={!!busy} onClick={() => void control(item, 'run_once')}>Run this review now</button>}
      {active(item.status) && <button className="button" disabled={!!busy} onClick={() => void control(item, 'cancel')}>Cancel this review</button>}
      {item.error && <p role="alert">{item.error}</p>}
      {item.result && <>{item.kind === 'evidence_retry' && item.result.status && <p><strong>Evidence outcome: {item.result.status === 'supported' ? 'Supported' : item.result.status === 'partial' ? 'Partly supported' : item.result.status === 'unresolved' ? 'Still unresolved' : item.result.status.replaceAll('_', ' ')}</strong></p>}<div className="research-action-sides">{item.result.bull_case && <div><h4>Case for</h4>{bullets(item.result.bull_case)}</div>}{item.result.bear_case && <div><h4>Case against</h4>{bullets(item.result.bear_case)}</div>}</div>
        {item.result.resolution && <div><h4>What the review concludes</h4>{typeof item.result.resolution === 'string' ? <p>{item.result.resolution}</p> : bullets([item.result.resolution])}</div>}
        {item.result.summary && <p>{item.result.summary}</p>}{item.result.findings && bullets(item.result.findings)}
        {!!item.result.open_questions?.length && <><h4>Still unanswered</h4>{bullets(item.result.open_questions)}</>}
        {!!item.result.what_would_change_mind?.length && <><h4>What would change the conclusion?</h4>{bullets(item.result.what_would_change_mind)}</>}
        {item.result.remaining_gap && <p>Still missing: {item.result.remaining_gap}</p>}
      </>}
    </article>)}
  </section>
}
