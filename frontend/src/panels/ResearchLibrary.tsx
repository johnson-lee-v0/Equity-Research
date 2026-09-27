import { Fragment, useEffect, useId, useMemo, useRef, useState, type MouseEvent } from 'react'
import { apiFetch } from '../api'
import type { CioPricePlan, Namespace, ValuationBlock } from '../types'
import PriceTargetCard from '../components/PriceTargetCard'
import InvestmentProcess from '../components/InvestmentProcess'
import { earningsReviewForRun } from '../components/investmentProcessModel'
import { libraryTargetIsLoading, type PriceTargetSource } from '../components/priceTargetModel'
import { bibliography, buildLibraryQuestions, buildSupportingQuestions, libraryReadingSections, record, type LibraryQuestion, type LibrarySource } from './libraryReadingModel'
import './research-library.css'

type Finding = {
  id: string; ticker: string; name: string; archived: boolean; disposition: string; summary: string
  researched_at?: string; updated_at?: string; revisions: number; status?: string; live?: boolean
}
type RetainedRow = { id: number | string; at?: string; updated_at?: string; kind?: string; run_id?: string; data: Record<string, unknown>; questions?: unknown[]; coverage_gaps?: string[] }
type Detail = {
  idea: RetainedRow; packets: RetainedRow[]; events: RetainedRow[]; questions?: unknown[]
  historical?: boolean; live?: boolean; runs?: { id: string; status: string; request?: string; created_at?: string; ticker?: string; origin_ref?: string; investment_process?: unknown }[]
  workflows?: { id: string; status: string; research_run_id?: string | null }[]
  coverage_gaps?: string[]
}
const label = (value: string) => ({ wait_price: 'Wait for price', wait_evidence: 'Wait for evidence', captured: 'Saved for research', running: 'Research in progress', queued: 'Queued for research', completed: 'Research complete' }[value] || value.replace(/([a-z])([A-Z])/g, '$1 $2').replaceAll('_', ' ').replace(/^./, (c) => c.toUpperCase()))
const date = (value?: string) => value && !Number.isNaN(Date.parse(value)) ? new Date(value).toLocaleDateString() : 'Date not recorded'
const inProgress = (status?: string) => ['pending', 'queued', 'running', 'waiting', 'researching', 'ideating'].includes(status || '')
const unanswered = (status?: string) => {
  if (['pending', 'queued'].includes(status || '')) return 'This question is queued for research. The answer and its sources will appear here.'
  if (inProgress(status)) return 'Research is in progress. The answer and its sources will appear here.'
  if (status === 'paused') return 'Research is paused. An answer has not been saved yet.'
  if (status === 'failed') return 'The latest research attempt did not produce an answer. Start a new review to try again.'
  return 'This question has not been answered in the saved research.'
}

function SourceEntry({ source, prefix }: { source: LibrarySource; prefix: string }) {
  return <li id={`${prefix}-${source.number}`} tabIndex={-1} className="library-source">
    <span className="library-source-number">[{source.number}]</span>
    <div>
      {source.url ? <a href={source.url} target="_blank" rel="noreferrer">{source.title}<span aria-hidden="true"> ↗</span></a> : <strong>{source.title}</strong>}
      <p className="library-source-meta">{[source.publisher || (source.url ? new URL(source.url).hostname.replace(/^www\./, '') : 'Retained evidence'), source.publishedAt].filter(Boolean).join(' · ')}</p>
      {source.locator && <p className="library-source-location">{source.locator}</p>}
      {source.quote && <blockquote>{source.quote}</blockquote>}
    </div>
  </li>
}

function QuestionCard({ question }: { question: LibraryQuestion }) {
  const isConclusion = question.kind === 'conclusion'
  const prefix = useId()
  const details = useRef<HTMLDetailsElement>(null)
  const lastCitation = useRef<HTMLAnchorElement | null>(null)
  function openSource(event: MouseEvent<HTMLAnchorElement>, number: number) {
    event.preventDefault()
    lastCitation.current = event.currentTarget
    if (details.current) details.current.open = true
    requestAnimationFrame(() => {
      const target = document.getElementById(`${prefix}-${number}`)
      target?.focus({ preventScroll: true })
      target?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    })
  }
  function citations(numbers: number[]) {
    return numbers.length > 0 && <span className="library-citations">{' '}{numbers.map((number) => <a key={number}
      href={`#${prefix}-${number}`} aria-label={`Source ${number} for ${question.question}`} aria-controls={`${prefix}-sources`}
      onClick={(event) => openSource(event, number)}>[{number}]</a>)}</span>
  }
  return <section className="library-question" aria-labelledby={`${prefix}-question`}>
    <div className="library-question-heading"><span className="library-eyebrow">{isConclusion ? 'Conclusion' : 'Question'}</span>
      <h3 id={`${prefix}-question`}>{question.question}</h3>
    </div>
    <div className="library-answer">
      {!isConclusion && <span className="library-eyebrow">Answer</span>}
      {question.segments.length ? question.segments.map((segment, index) => <div key={index} className={segment.label ? 'library-answer-context' : undefined}>
        {segment.label && <h4>{segment.label}</h4>}
        <p>{segment.parts ? segment.parts.map((part, i) => <Fragment key={i}>{part.text}{citations(part.citations)}</Fragment>) : <>{segment.text}{citations(segment.citations)}</>}</p>
      </div>) : <p className="library-unanswered">{unanswered(question.status)}</p>}
      {question.asOf && <p className="library-answer-date">{question.segments.length ? 'Research as of' : 'Saved'} {date(question.asOf)}</p>}
    </div>
    {question.sources.length > 0 ? <details className="library-sources" id={`${prefix}-sources`} ref={details}>
      <summary>Sources <span>{question.sources.length}</span></summary>
      <ol>{question.sources.map((source) => <SourceEntry key={source.number} source={source} prefix={prefix} />)}</ol>
      <button type="button" className="library-back-link" onClick={() => {
        if (lastCitation.current) lastCitation.current.focus()
        else document.getElementById(`${prefix}-question`)?.scrollIntoView({ block: 'nearest' })
      }}>Back to answer ↑</button>
    </details> : question.segments.length > 0 && <p className="library-source-note">No source links were attached to this answer.</p>}
  </section>
}

function ReviewHistory({ events }: { events: RetainedRow[] }) {
  return <details className="library-history"><summary>Decision history <span>{events.length}</span></summary>
    {!events.length && <p className="library-muted">No earlier decisions have been recorded.</p>}
    {events.map((event) => {
      const data = event.data
      const summary = typeof data.summary === 'string' ? data.summary : typeof data.message === 'string' ? data.message : typeof data.reason === 'string' ? data.reason : ''
      return <div key={event.id} className="library-event"><div><strong>{label(event.kind || String(data.kind || 'Research update'))}</strong><time>{date(event.at)}</time></div>
        {summary && <p>{summary}</p>}{typeof data.disposition === 'string' && <span className="library-muted">{label(data.disposition)}</span>}
      </div>
    })}
  </details>
}

export default function ResearchLibrary({ onResearch, namespace }: { onResearch: (question: string) => void; namespace: Namespace }) {
  const detailRef = useRef<HTMLElement>(null)
  const focused = useRef<string | null>(null)
  const [items, setItems] = useState<Finding[]>([])
  const [loaded, setLoaded] = useState(false)
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [detail, setDetail] = useState<Detail | null>(null)
  const [revision, setRevision] = useState('current')
  const [listError, setListError] = useState('')
  const [detailError, setDetailError] = useState('')
  const error = [...new Set([listError, detailError].filter(Boolean))].join(' ')
  useEffect(() => {
    if (!detail || focused.current === selected) return
    focused.current = selected
    detailRef.current?.focus({ preventScroll: true })
    detailRef.current?.scrollIntoView({ block: 'start' })
  }, [detail, selected])
  useEffect(() => {
    setItems([])
    setLoaded(false)
    setSelected(null)
    setDetail(null)
    setListError('')
    setDetailError('')
    focused.current = null
    const controller = new AbortController()
    const refresh = () => apiFetch<{ items: Finding[] }>(`/api/research-library?namespace=${encodeURIComponent(namespace)}`, {}, { signal: controller.signal })
      .then((data) => { if (!controller.signal.aborted) { setItems(data.items); setLoaded(true); setListError('') } })
      .catch((e) => { if (!controller.signal.aborted) { setListError(String(e.message)); setLoaded(true) } })
    void refresh()
    const timer = window.setInterval(refresh, 10000)
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [namespace])
  useEffect(() => {
    setDetail(null)
    setDetailError('')
    setRevision('current')
    if (!selected) return
    const controller = new AbortController()
    const refresh = () => apiFetch<Detail>(`/api/research-library/${encodeURIComponent(selected)}?namespace=${encodeURIComponent(namespace)}`, {}, { signal: controller.signal })
      .then((data) => { if (!controller.signal.aborted) { setDetail(data); setDetailError('') } }).catch((e) => { if (!controller.signal.aborted) setDetailError(String(e.message)) })
    void refresh()
    const timer = window.setInterval(refresh, 10000)
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [selected, namespace])
  const matches = items.filter((item) => `${item.ticker} ${item.name} ${item.summary}`.toLowerCase().includes(query.toLowerCase()))
  const idea = detail?.idea.data
  const selectedRevision = detail?.packets.find((p) => String(p.id) === revision)
  const packet = revision === 'current' ? idea?.packet : selectedRevision?.data
  const targetPacket = record(packet)
  const targetSources: PriceTargetSource[] = (Array.isArray(targetPacket.target_sources) ? targetPacket.target_sources : []).map((value) => {
    const source = record(value)
    return { ...source, source_version: source.source_version == null ? null : String(source.source_version) } as PriceTargetSource
  })
  const targetGaps = Array.isArray(targetPacket.target_citation_gaps) ? targetPacket.target_citation_gaps.filter((gap): gap is string => typeof gap === 'string') : []
  const current = items.find((item) => item.id === selected)
  const savedQuestions = revision === 'current' ? detail?.questions : selectedRevision?.questions
  const questions = useMemo(() => buildLibraryQuestions(packet, idea, savedQuestions), [packet, idea, savedQuestions])
  const readingSections = useMemo(() => libraryReadingSections(questions), [questions])
  const supporting = useMemo(() => buildSupportingQuestions(packet), [packet])
  const sources = useMemo(() => bibliography(packet, idea), [packet, idea])
  const latestRun = detail?.runs?.[0]
  const selectedPacket = revision === 'current' ? detail?.packets?.[0] : selectedRevision
  const assessmentRun = detail?.runs?.find((run) => run.id === selectedPacket?.run_id) || (!selectedPacket && revision === 'current' ? latestRun : undefined)
  const receipt = earningsReviewForRun(assessmentRun, current?.ticker)
  const linkedWorkflow = receipt?.workflow_id ? detail?.workflows?.find((workflow) => workflow.id === receipt.workflow_id) : undefined
  const earningsReview = receipt && { ...receipt, status: receipt.status === 'saved' && linkedWorkflow ? linkedWorkflow.status : receipt.status }
  const researchStatus = latestRun?.status || current?.status
  const pending = inProgress(researchStatus) || researchStatus === 'paused'
  const targetLoading = libraryTargetIsLoading({ revision, packetProvenance: targetPacket.target_provenance, packetRunId: detail?.packets?.[0]?.run_id, latestRunId: latestRun?.id, runStatus: researchStatus, hasPackets: !!detail?.packets?.length })
  const coverageGaps = (revision === 'current' ? detail?.coverage_gaps : selectedRevision?.coverage_gaps) || []
  const sourcePrefix = useId()
  function download() {
    if (!detail) return
    const url = URL.createObjectURL(new Blob([JSON.stringify(detail, null, 2)], { type: 'application/json' }))
    const link = document.createElement('a'); link.href = url; link.download = `research-${current?.ticker || 'finding'}.json`; link.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  return <section className="research-library">
    <header><h1>Company history</h1><p>Every company, its questions and the evidence behind the answers. Research and earlier reviews stay together as the work develops.</p></header>
    {error && <p role="alert" className="library-error">{error}</p>}
    <label className="library-search">Find a company or question<input type="search" placeholder="Ticker, company or keyword" value={query} onChange={(e) => setQuery(e.target.value)} /></label>
    {!loaded ? <p role="status">Loading saved research…</p> : !items.length ? <p>No companies saved yet. Bring up a ticker in your research to start its library record.</p> : <div className="library-layout">
      <div className="library-list"><p className="library-muted">{matches.length} of {items.length} companies</p>
        {!matches.length && <div className="library-empty" role="status"><h2>No matching research</h2><p>Try a company name, ticker or a shorter keyword.</p><button type="button" className="button" onClick={() => setQuery('')}>Clear search</button></div>}
        {matches.map((item) => <button key={item.id} className={`library-item ${selected === item.id ? 'selected' : ''}`} onClick={() => { setDetailError(''); setSelected(item.id) }} aria-pressed={selected === item.id} aria-label={`Open ${item.ticker}${item.name && item.name !== item.ticker ? ` · ${item.name}` : ''} research`}>
          <strong>{item.ticker} <span>{item.name}</span></strong><span>{label(inProgress(item.status) ? item.status! : item.disposition || item.status || 'Research pending')}{item.archived ? ' · Archived' : ''}</span>
          <p>{item.summary || 'Research questions and evidence will collect here.'}</p><small>{date(item.researched_at || item.updated_at)} · {item.revisions} saved {item.revisions === 1 ? 'revision' : 'revisions'}</small>
        </button>)}
      </div>
      <article className="library-detail" ref={detailRef} tabIndex={-1} aria-label="Selected research finding">
        {!selected ? <div className="library-placeholder"><span className="library-eyebrow">Company research</span><h2>Open a company</h2><p>Read its questions and answers, then expand a source when you want the evidence.</p></div> : !detail ? <p role="status">Loading research history…</p> : <>
          <div className="library-detail-title"><div><h2>{current?.ticker} <span>{current?.name}</span></h2><p className="library-muted">{detail.live ? 'Ongoing company record' : 'Saved research'} · {date(current?.researched_at || current?.updated_at)}</p></div>
            <div className="library-actions"><button className="button" onClick={download}>Export finding</button><button className="button button-primary" onClick={() => onResearch(`Reassess ${current?.ticker || ''} ${current?.name || ''} using current evidence. Previous research summary (historical, verify before relying on it): ${current?.summary || 'No saved conclusion.'}`)}>Start new review</button></div>
          </div>
          <InvestmentProcess ticker={current?.ticker} earnings={earningsReview} latestEarningsId={revision === 'current' ? detail.workflows?.[0]?.id : undefined} latestEarningsStatus={revision === 'current' ? detail.workflows?.[0]?.status : undefined}
            questionCount={readingSections.questions.length} answeredCount={readingSections.questions.filter((question) => question.segments.some((segment) => !segment.label)).length}
            pricingRecorded={['complete', 'ready'].includes(String(record(targetPacket.valuation).status)) || !!record(targetPacket.future_target).price}
            decisionRecorded={targetPacket.target_provenance === 'canonical_case'} />
          {pending && <div className="library-progress" role="status"><strong>{researchStatus === 'paused' ? 'Research is paused' : ['pending', 'queued'].includes(researchStatus || '') ? 'Research is queued' : 'Research is in progress'}</strong><p>This company is saved. New answers and sources will appear here as the research loop completes.</p></div>}
          <div className="library-reading-tools"><label>Research revision<select value={revision} onChange={(e) => setRevision(e.target.value)}><option value="current">Latest saved research</option>{detail.packets.map((p, i) => <option key={p.id} value={String(p.id)}>Revision {detail.packets.length - i} · {date(p.at)}</option>)}</select></label><p>{readingSections.questions.length} {readingSections.questions.length === 1 ? 'question' : 'questions'}{readingSections.conclusions.length > 0 ? ' · Saved conclusion' : ''} · Select a citation to see its source</p></div>
          <div className="library-packet" key={`${selected}-${revision}`}>
            {readingSections.conclusions.map((conclusion) => <QuestionCard key={conclusion.id} question={conclusion} />)}
            {Boolean(targetLoading || targetPacket.target_provenance || targetPacket.valuation || targetPacket.future_target) && <PriceTargetCard valuation={targetPacket.valuation as ValuationBlock | null} futureTarget={targetPacket.future_target as CioPricePlan | null} horizon={typeof targetPacket.horizon === 'string' ? targetPacket.horizon : null} asOf={typeof targetPacket.researchedAt === 'string' ? targetPacket.researchedAt : null} ticker={current?.ticker} namespace={namespace} sources={targetSources} citationGaps={targetGaps} loading={targetLoading} />}
            {coverageGaps.length > 0 && <details className="library-coverage"><summary>Evidence gaps <span>{coverageGaps.length}</span></summary><p>This review used the available evidence. These limits remain unresolved.</p><ul>{coverageGaps.map((gap) => <li key={gap}>{gap}</li>)}</ul></details>}
            {readingSections.questions.length ? readingSections.questions.map((question, index) => <QuestionCard key={`${question.id}-${index}`} question={question} />) : !readingSections.conclusions.length ? <section className="library-question"><span className="library-eyebrow">Question</span><h3>What is the investment case for {current?.ticker}?</h3><div className="library-answer"><span className="library-eyebrow">Answer</span><p className="library-unanswered">The company is saved. A researched answer has not been recorded yet.</p></div></section> : null}
            {supporting.length > 0 && <details className="library-supporting"><summary>Supporting analysis <span>{supporting.length} questions</span></summary>{supporting.map((question) => <QuestionCard key={question.id} question={question} />)}</details>}
            {sources.length > 0 && <details className="library-bibliography"><summary>All saved research sources <span>{sources.length}</span></summary><p className="library-source-note">The saved bibliography. Citations within each answer identify the sources attached to that answer.</p><ol>{sources.map((source) => <SourceEntry key={source.number} source={source} prefix={sourcePrefix} />)}</ol></details>}
            <ReviewHistory events={detail.events} />
            {typeof record(packet).latestPeriod === 'string' && <p className="library-footnote">Reporting period: {String(record(packet).latestPeriod)}</p>}
          </div>
        </>}
      </article>
    </div>}
  </section>
}
