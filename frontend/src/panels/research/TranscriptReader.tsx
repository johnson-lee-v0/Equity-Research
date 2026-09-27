import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { apiFetch } from '../../api'
import type { PlainLanguageBriefs, TranscriptResult, TranscriptSentence, TranscriptTurn } from './DocumentViews'
import {
  buildReadingModel,
  discussionExcerpts,
  discussionMatchesOutsideBrief,
  groupEvidence,
  negativeSentences,
  orderDiscussionGroups,
  roleLabel,
  transcriptPageForSentence,
  transcriptPageForTurn,
} from './transcriptContext'
import type { EvidenceGroup } from './transcriptContext'
import { managementHighlights } from './transcriptHighlights'
import { EmptyState } from './shared'
import './transcript-reader.css'

type Tab = 'themes' | 'negative' | 'transcript'
type ReturnPoint = {
  tab: Tab
  context: EvidenceGroup | null
  scrollY: number
  focusElement?: HTMLElement
  transcriptQuery: string
  transcriptPage: number
}
const EVIDENCE_PAGE_SIZE = 3
const TRANSCRIPT_PAGE_SIZE = 8
const TABS: { id: Tab; label: string }[] = [
  { id: 'themes', label: 'Business themes' },
  { id: 'negative', label: 'Negative language' },
  { id: 'transcript', label: 'Full transcript' },
]

function Highlight({ text, needle, markId, startIndex }: { text: string; needle?: string; markId?: string; startIndex?: number | null }) {
  if (!needle?.trim()) return <>{text}</>
  const index = startIndex === null ? -1 : typeof startIndex === 'number'
    ? text.slice(startIndex, startIndex + needle.length).toLocaleLowerCase() === needle.toLocaleLowerCase() ? startIndex : -1
    : text.toLocaleLowerCase().indexOf(needle.toLocaleLowerCase())
  return index < 0 ? (
    <>{text}</>
  ) : (
    <>
      {text.slice(0, index)}
      <mark id={markId}>{text.slice(index, index + needle.length)}</mark>
      {text.slice(index + needle.length)}
    </>
  )
}

function Pager({
  page,
  total,
  size,
  noun,
  onPage,
  jump = false,
}: {
  page: number
  total: number
  size: number
  noun: string
  onPage: (page: number) => void
  jump?: boolean
}) {
  if (!total) return null
  const pages = Math.ceil(total / size)
  const first = page * size + 1
  const last = Math.min((page + 1) * size, total)
  return (
    <nav className="transcript-pager" aria-label={`${noun} pages`}>
      <span aria-live="polite">
        {first === last ? first : `${first}–${last}`} of {total} {noun}
      </span>
      <div>
        <button
          type="button"
          className="button"
          disabled={page === 0}
          onClick={() => onPage(page - 1)}
        >
          Previous
        </button>
        {jump && pages > 1 ? (
          <label className="transcript-page-select">
            Page{' '}
            <select
              aria-label={`${noun} page`}
              value={page}
              onChange={(event) => onPage(Number(event.target.value))}
            >
              {Array.from({ length: pages }, (_, index) => (
                <option key={index} value={index}>
                  {index + 1} of {pages}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <span>
            Page {page + 1} of {pages}
          </span>
        )}
        <button
          type="button"
          className="button"
          disabled={page + 1 === pages}
          onClick={() => onPage(page + 1)}
        >
          Next
        </button>
      </div>
    </nav>
  )
}

function Turn({
  turn,
  highlight,
  highlightStart,
  selected = false,
  elementId,
}: {
  turn: TranscriptTurn
  highlight?: string
  highlightStart?: number | null
  selected?: boolean
  elementId?: string
}) {
  return (
    <article
      className={`transcript-turn${selected ? ' is-cited' : ''}`}
      id={elementId}
      tabIndex={selected ? -1 : undefined}
    >
      <header>
        <strong>{turn.speaker}</strong>
        <span className={`transcript-role ${turn.role}`}>
          {roleLabel(turn.role, turn.statement_kind)}
        </span>
        <span>{turn.section}</span>
        {selected && <span className="transcript-citation-label">Cited passage</span>}
      </header>
      <p>
        <Highlight
          text={turn.text}
          needle={highlight}
          startIndex={highlightStart}
          markId={selected && elementId ? `${elementId}-citation` : undefined}
        />
      </p>
    </article>
  )
}

export function TranscriptView({
  result,
  ticker,
  focusSentenceId,
  focusRequest,
  transcriptDownloadUrl,
  analysisId,
  fiscalPeriod,
  earningsDate,
}: {
  result: TranscriptResult
  ticker?: string
  focusSentenceId?: string | number
  focusRequest?: number
  transcriptDownloadUrl?: string
  analysisId?: string
  fiscalPeriod?: string
  earningsDate?: string
}) {
  const instanceId = useId()
  const [briefs, setBriefs] = useState<PlainLanguageBriefs | undefined>(result.plain_language)
  const [explaining, setExplaining] = useState(false)
  const [explanationError, setExplanationError] = useState('')
  const [quoteFocus, setQuoteFocus] = useState<{ text: string; offset: number } | null>(null)
  useEffect(() => { setBriefs(result.plain_language); setExplanationError('') }, [result.source_hash, result.plain_language])
  async function explainCall() {
    if (!analysisId || explaining) return
    setExplaining(true); setExplanationError('')
    try { setBriefs(await apiFetch<PlainLanguageBriefs>(`/api/document-analysis/history/${encodeURIComponent(analysisId)}/explain`, { method: 'POST', body: '{}' }, { mutation: true })) }
    catch (error) { setExplanationError(error instanceof Error ? error.message : 'Explanation unavailable. Original words remain available.') }
    finally { setExplaining(false) }
  }
  const model = useMemo(() => buildReadingModel(result), [result])
  const highlights = useMemo(() => managementHighlights(result, briefs), [result, briefs])
  const [tab, setTab] = useState<Tab>('themes')
  const [themeIndex, setThemeIndex] = useState(0)
  const [themePage, setThemePage] = useState(0)
  const [discussionOrder, setDiscussionOrder] = useState('questions')
  const [negativeScope, setNegativeScope] = useState('management')
  const [negativeQuery, setNegativeQuery] = useState('')
  const [negativePage, setNegativePage] = useState(0)
  const [transcriptQuery, setTranscriptQuery] = useState('')
  const [transcriptPage, setTranscriptPage] = useState(0)
  const [context, setContext] = useState<EvidenceGroup | null>(null)
  const [highlightId, setHighlightId] = useState<string | null>(null)
  const [highlightTurnId, setHighlightTurnId] = useState<string | null>(null)
  const [returnPoint, setReturnPoint] = useState<ReturnPoint | null>(null)
  const [jumpNotice, setJumpNotice] = useState('')
  const [sourceScrollRequest, setSourceScrollRequest] = useState(0)
  const contextReturn = useRef<{ scrollY: number; focusId: string } | null>(null)
  const readerRef = useRef<HTMLElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const contextRef = useRef<HTMLDivElement>(null)
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([])
  const lastExternalFocus = useRef('')
  const resultIdentity =
    result.source_hash || `${result.sentences[0]?.text}:${result.sentences.length}`
  const priorResultIdentity = useRef(resultIdentity)

  useEffect(() => {
    if (priorResultIdentity.current === resultIdentity) return
    priorResultIdentity.current = resultIdentity
    lastExternalFocus.current = ''
    setTab('themes')
    setThemeIndex(0)
    setThemePage(0)
    setNegativePage(0)
    setNegativeQuery('')
    setTranscriptPage(0)
    setTranscriptQuery('')
    setContext(null)
    setReturnPoint(null)
    setHighlightId(null)
    setHighlightTurnId(null)
    setQuoteFocus(null)
    setJumpNotice('')
  }, [resultIdentity])

  const theme = result.themes[Math.min(themeIndex, Math.max(0, result.themes.length - 1))]
  const themeMatches =
    theme?.evidence
      .map((item) => model.sentenceById.get(String(item.sentence_id)))
      .filter((item): item is TranscriptSentence => Boolean(item)) || []
  const matches =
    tab === 'negative' ? negativeSentences(result, negativeScope, negativeQuery) : themeMatches
  const groups = orderDiscussionGroups(groupEvidence(matches, model), model, discussionOrder === 'questions')
  const currentEvidencePage = Math.min(
    tab === 'negative' ? negativePage : themePage,
    Math.max(0, Math.ceil(groups.length / EVIDENCE_PAGE_SIZE) - 1),
  )
  const filteredTurns = model.turns.filter(
    (turn) =>
      !transcriptQuery.trim() ||
      `${turn.text} ${turn.speaker}`
        .toLocaleLowerCase()
        .includes(transcriptQuery.trim().toLocaleLowerCase()),
  )
  const currentTranscriptPage = Math.min(
    transcriptPage,
    Math.max(0, Math.ceil(filteredTurns.length / TRANSCRIPT_PAGE_SIZE) - 1),
  )
  const highlightedTurn = highlightTurnId ? model.turnById.get(highlightTurnId) : highlightId ? model.turnBySentence.get(highlightId) : undefined
  const highlightedSentence = highlightId ? model.sentenceById.get(highlightId) : undefined
  const heading =
    tab === 'negative' ? 'Negative language, in context' : theme?.name || 'Business themes'
  const contextSentence = context?.matches[0]
  const transcriptFilename = `${ticker || 'earnings-call'}-${model.fullTextAvailable ? 'transcript' : 'retained-passages'}.txt`

  // Brief links update state from an effect. Wait for the new page and citation
  // to be committed before scrolling; a frame queued by that effect can run too early.
  useLayoutEffect(() => {
    if (!sourceScrollRequest || tab !== 'transcript' || !highlightedTurn) return
    const targetElement = document.getElementById(`${instanceId}-${highlightedTurn.id}`)
    const citation = document.getElementById(`${instanceId}-${highlightedTurn.id}-citation`)
    targetElement?.focus({ preventScroll: true })
    // Keep the speaker name in view when the citation starts their turn.
    ;(quoteFocus?.offset === 0 ? targetElement : citation || targetElement)?.scrollIntoView({ block: 'start' })
  }, [
    sourceScrollRequest,
    tab,
    currentTranscriptPage,
    highlightId,
    highlightedTurn?.id,
    instanceId,
  ])

  function scrollPanel() {
    requestAnimationFrame(() => {
      panelRef.current?.focus({ preventScroll: true })
      panelRef.current?.scrollIntoView({ block: 'start' })
    })
  }
  function selectTab(next: Tab) {
    setTab(next)
    setContext(null)
    setReturnPoint(null)
    setHighlightId(null)
    setHighlightTurnId(null)
    setQuoteFocus(null)
    setJumpNotice('')
  }
  function tabKey(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const next =
      event.key === 'ArrowRight'
        ? (index + 1) % TABS.length
        : event.key === 'ArrowLeft'
          ? (index + TABS.length - 1) % TABS.length
          : event.key === 'Home'
            ? 0
            : event.key === 'End'
              ? TABS.length - 1
              : -1
    if (next < 0) return
    event.preventDefault()
    selectTab(TABS[next].id)
    tabRefs.current[next]?.focus()
  }
  function openContext(group: EvidenceGroup, focusId: string) {
    contextReturn.current = { scrollY: window.scrollY, focusId }
    setContext(group)
    requestAnimationFrame(() => {
      contextRef.current?.focus({ preventScroll: true })
      contextRef.current?.scrollIntoView({ block: 'start' })
    })
  }
  function backToEvidence() {
    setContext(null)
    const prior = contextReturn.current
    requestAnimationFrame(() => {
      if (prior) {
        document.getElementById(prior.focusId)?.focus({ preventScroll: true })
        window.scrollTo({ top: prior.scrollY })
      }
    })
  }
  function jumpToSentence(sentenceId: string | number) {
    const target = transcriptPageForSentence(model, sentenceId, TRANSCRIPT_PAGE_SIZE)
    if (!target?.turn) {
      setJumpNotice('This citation is unavailable in the saved transcript.')
      return
    }
    jumpToTurn(target.turn.id, sentenceId)
  }
  function jumpToTurn(turnId: string, sentenceId?: string | number, quote?: { text: string; offset: number }) {
    const target = transcriptPageForTurn(model, turnId, TRANSCRIPT_PAGE_SIZE)
    if (!target?.turn) {
      setJumpNotice('This citation is unavailable in the saved transcript.')
      return
    }
    setReturnPoint({
      tab,
      context,
      scrollY: window.scrollY,
      focusElement:
        document.activeElement instanceof HTMLElement ? document.activeElement : undefined,
      transcriptQuery,
      transcriptPage,
    })
    setTab('transcript')
    setContext(null)
    setTranscriptQuery('')
    setTranscriptPage(target.page)
    setHighlightId(sentenceId == null ? null : String(sentenceId))
    setHighlightTurnId(turnId)
    setQuoteFocus(quote || null)
    setSourceScrollRequest((request) => request + 1)
    setJumpNotice('Showing the cited passage in the transcript. Search has been cleared.')
  }
  useEffect(() => {
    if (focusSentenceId == null) return
    const key = `${focusSentenceId}:${focusRequest ?? 0}`
    if (lastExternalFocus.current === key) return
    lastExternalFocus.current = key
    jumpToSentence(focusSentenceId)
    // External quote requests are intentional events, not a reset on every result render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusSentenceId, focusRequest])

  function returnFromTranscript() {
    if (!returnPoint) return
    setTab(returnPoint.tab)
    setContext(returnPoint.context)
    setTranscriptQuery(returnPoint.transcriptQuery)
    setTranscriptPage(returnPoint.transcriptPage)
    setHighlightId(null)
    setHighlightTurnId(null)
    setQuoteFocus(null)
    setReturnPoint(null)
    setJumpNotice('')
    requestAnimationFrame(() => {
      const prior = returnPoint.focusElement
      const target = prior?.isConnected ? prior : prior?.id ? document.getElementById(prior.id) : null
      let ancestor = target?.parentElement
      while (ancestor) {
        if (ancestor instanceof HTMLDetailsElement) ancestor.open = true
        ancestor = ancestor.parentElement
      }
      target?.focus({ preventScroll: true })
      window.scrollTo({ top: returnPoint.scrollY })
    })
  }
  function downloadTranscript() {
    const url = URL.createObjectURL(
      new Blob([model.transcriptText], { type: 'text/plain;charset=utf-8' }),
    )
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = transcriptFilename
    anchor.hidden = true
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  function evidencePage(page: number) {
    if (tab === 'negative') setNegativePage(page)
    else setThemePage(page)
    scrollPanel()
  }

  function evidenceCard(group: EvidenceGroup) {
    const first = group.matches[0]
    const excerpts = discussionExcerpts(group, model)
    const simple = briefs?.source_hash === result.source_hash ? briefs?.discussions[group.id] : undefined
    const matchesOutsideBrief = discussionMatchesOutsideBrief(group, excerpts)
    const triggerId = `${instanceId}-context-${group.id}`
    return (
      <article className="transcript-evidence-card" key={group.id}>
        <div className="transcript-evidence-meta">
          <span>{group.exchange ? 'Question & answer' : first.section || 'Prepared remarks'}</span>
          <span>
            {group.matches.length} matching {group.matches.length === 1 ? 'passage' : 'passages'}
          </span>
        </div>
        {simple && <section className="transcript-simple-brief" aria-label="Discussion summary bullets">
          <h4>The main points</h4>
          <ul>{simple.bullets.map((bullet, index) => <li key={index}>
            <p><strong>{bullet.kind === 'question' ? 'They asked: ' : bullet.kind === 'answer' ? 'Management said: ' : 'What management said: '}</strong>{bullet.summary}</p>
            <details className="transcript-brief-context"><summary>See the words behind this</summary>
              {bullet.quotes.map((quote, quoteIndex) => <blockquote key={quoteIndex}><strong>{quote.speaker}</strong><p>“{quote.quote}” <button type="button" id={`${triggerId}-simple-${index}-${quoteIndex}`} className="transcript-inline-source transcript-text-button" onClick={() => jumpToTurn(quote.turn_id, undefined, {text: quote.quote, offset: quote.offset})}>Full transcript ↗</button></p></blockquote>)}
            </details>
          </li>)}</ul>
          {simple.answer_status === 'partial' && <p className="transcript-cues">Some parts of the question were left unanswered.</p>}
          {simple.answer_status === 'unclear' && <p className="transcript-cues">It is unclear whether the response answers the question.</p>}
          {simple.answer_status === 'no_response' && <p className="transcript-cues">No management answer was found in this discussion.</p>}
          <small className="transcript-cues">Plain-language interpretation. Open the quotes to check the meaning.</small>
        </section>}
        {!simple && <p className="transcript-cues">A plain-language explanation has not been saved for this discussion. Expand the original words below to read the discussion.</p>}
        <details className="transcript-original-discussion">
          <summary><span className="transcript-brief-heading"><strong>{group.exchange ? 'Question & answer' : 'Management remarks'}</strong><span>The original words, with speaker context</span></span></summary>
        <div className="transcript-brief">
          {excerpts.map((excerpt) => <div className={`transcript-brief-row transcript-brief-${excerpt.turn.role}`} key={excerpt.turn.id}>
            <div className="transcript-brief-attribution"><strong>{excerpt.label}</strong><span>{excerpt.turn.speaker}</span></div>
            {excerpt.fullContextRetained && <p className="transcript-cues">Full {excerpt.turn.role === 'management' && group.exchange ? 'answer' : 'speaker turn'} retained for context.</p>}
            <blockquote>{excerpt.omittedBefore && <span aria-label="Earlier text omitted">… </span>}<Highlight text={excerpt.text} needle={excerpt.highlight} startIndex={excerpt.highlightStart ?? null} />{excerpt.omittedAfter && <span aria-label="More text follows"> …</span>}{' '}
              <button type="button" id={`${triggerId}-quote-${excerpt.turn.id}`} className="transcript-inline-source transcript-text-button" onClick={() => jumpToTurn(excerpt.turn.id, excerpt.sentenceId)} aria-label={`Read ${excerpt.turn.speaker}'s ${excerpt.label === 'Analyst asked' ? 'question' : 'remarks'} in the full transcript`}>Full transcript ↗</button>
            </blockquote>
            {(excerpt.omittedBefore || excerpt.omittedAfter) && <details className="transcript-brief-context"><summary>Show this speaker’s full context</summary><p>{excerpt.turn.text}</p></details>}
          </div>)}
          {!excerpts.length && <blockquote>{first.text} <button type="button" id={`${triggerId}-fallback-${first.id}`} className="transcript-inline-source transcript-text-button" onClick={() => jumpToSentence(first.id)}>Full transcript ↗</button></blockquote>}
        </div>
        </details>
        {tab === 'negative' && <p className="transcript-cues">Matched wording: {[...new Set(group.matches.flatMap((sentence) => (sentence.sentiment.cues || []).map((cue) => `${cue.negated ? 'negated ' : ''}${cue.term}`)))].join(', ') || 'Negative wording in the saved source passage'}. Responses are included regardless of tone.</p>}
        {(group.matches.length > 1 || matchesOutsideBrief.length > 0) && <details className="transcript-matched-passages"><summary>{group.matches.length === 1 ? 'Matching passage outside the quoted summary' : `All ${group.matches.length} matching passages`}</summary>{group.matches.map((sentence) => <div className="transcript-quote" key={sentence.id}><strong>{sentence.speaker || 'Unattributed'} · {roleLabel(sentence.role, sentence.statement_kind)}</strong><blockquote>{sentence.text} <button type="button" id={`${triggerId}-match-${sentence.id}`} className="transcript-inline-source transcript-text-button" onClick={() => jumpToSentence(sentence.id)}>Full transcript ↗</button></blockquote></div>)}</details>}
        {group.exchange && !group.exchange.answered && (
          <p className="transcript-gap">
            No identifiable management response follows this question in the saved transcript.
          </p>
        )}
        <div className="transcript-card-actions">
          <button
            type="button"
            id={triggerId}
            className="button"
            onClick={() => openContext(group, triggerId)}
          >
            {group.exchange ? 'Read full question & answer' : 'Read full remarks'}{' '}
            <span aria-hidden="true">→</span>
          </button>
          <button
            type="button"
            className="transcript-text-button"
            id={`${triggerId}-source`}
            onClick={() => jumpToSentence(first.id)}
          >
            View in transcript
          </button>
        </div>
      </article>
    )
  }

  return (
    <section className="transcript-reader" ref={readerRef} aria-label="Earnings call reader">
      <section className="transcript-management-highlights" aria-labelledby={`${instanceId}-management-highlights`}>
        <header>
          <div><p className="transcript-highlights-kicker">Before the analyst questions</p><h2 id={`${instanceId}-management-highlights`}>Quarter & year highlights</h2></div>
          {(fiscalPeriod || earningsDate) && <span>{[fiscalPeriod, earningsDate?.slice(0, 10)].filter(Boolean).join(' · ')}</span>}
        </header>
        <p className="transcript-highlights-intro">What management reported in its opening remarks{ticker ? ` for ${ticker}` : ''}. Guidance and comparisons keep the periods stated on this call.</p>
        {highlights.length ? <ul>{highlights.map((highlight, index) => <li key={highlight.id}>
          <p>{highlight.text}</p>
          <details className="transcript-brief-context"><summary>{highlight.interpretation ? 'See management’s words' : 'Selected management wording · see source'}</summary>
            {highlight.quotes.map((quote, quoteIndex) => <blockquote key={`${quote.turn_id}-${quoteIndex}`}><strong>{quote.speaker}</strong><p>“{quote.quote}” <button type="button" id={`${instanceId}-highlight-${index}-${quoteIndex}`} className="transcript-inline-source transcript-text-button" onClick={() => jumpToTurn(quote.turn_id, undefined, { text: quote.quote, offset: quote.offset })}>Full transcript ↗</button></p></blockquote>)}
          </details>
        </li>)}</ul> : <p className="transcript-gap">No source-bound business highlights could be identified in the saved opening remarks. Read the transcript to review management’s presentation.</p>}
        {!!highlights.length && <small className="transcript-cues">Selected highlights, not a complete list. Open a quote for attribution and the full transcript for surrounding context.</small>}
      </section>
      <div className="transcript-explanation-toolbar">
        <div><strong>Understand the call</strong><p>Read the main points first. Expand the original question and answer or open a source quote to check a summary.</p></div>
        {analysisId && briefs?.status !== 'completed' && <button type="button" className="button" disabled={explaining} onClick={() => void explainCall()}>{explaining ? 'Preparing explanations…' : briefs?.status === 'partial' || briefs?.status === 'failed' ? 'Retry missing explanations' : 'Explain in simple words'}</button>}
        {explanationError && <p role="alert">{explanationError}</p>}
        {briefs?.gaps?.length ? <details><summary>Explanation coverage · {briefs.gaps.length} gaps</summary><ul>{briefs.gaps.map((gap, i) => <li key={i}>{gap}</li>)}</ul></details> : null}
      </div>
      <div className="transcript-navigation">
        <div className="transcript-nav-heading">
          <h2>Explore the call</h2>
          <span>
            {model.turns.length} speaker turns · {result.sentences.length} analyzed sentences
          </span>
        </div>
        <div className="transcript-tabs" role="tablist" aria-label="Explore earnings call">
          {TABS.map((item, index) => (
            <button
              type="button"
              key={item.id}
              ref={(element) => {
                tabRefs.current[index] = element
              }}
              id={`${instanceId}-tab-${item.id}`}
              role="tab"
              aria-selected={tab === item.id}
              aria-controls={`${instanceId}-panel`}
              tabIndex={tab === item.id ? 0 : -1}
              onClick={() => selectTab(item.id)}
              onKeyDown={(event) => tabKey(event, index)}
            >
              {item.label}
            </button>
          ))}
        </div>
        {returnPoint && tab === 'transcript' && (
          <button type="button" className="transcript-back" onClick={returnFromTranscript}>
            ←{' '}
            {returnPoint.context
              ? 'Back to question & answer'
              : returnPoint.tab === 'themes'
                ? 'Back to themes'
                : returnPoint.tab === 'negative'
                  ? 'Back to negative language'
                  : 'Back to previous reading'}
          </button>
        )}
      </div>
      <div
        className="transcript-panel"
        id={`${instanceId}-panel`}
        ref={panelRef}
        role="tabpanel"
        aria-labelledby={`${instanceId}-tab-${tab}`}
        tabIndex={-1}
      >
        {!model.fullTextAvailable && (
          <p className="transcript-gap">
            This older analysis contains retained passages only. The original full transcript is
            unavailable in this record.
          </p>
        )}
        {jumpNotice && (
          <p className="transcript-notice" role="status">
            {jumpNotice}
          </p>
        )}
        {tab === 'transcript' ? (
          <>
            <div className="transcript-section-heading">
              <div>
                <h3>
                  {model.fullTextAvailable
                    ? 'Full call transcript'
                    : 'Retained transcript passages'}
                </h3>
                <p>Read every speaker turn, including questions and responses.</p>
              </div>
              {transcriptDownloadUrl ? (
                <a className="button" href={transcriptDownloadUrl} download={transcriptFilename}>
                  Download {model.fullTextAvailable ? 'transcript' : 'passages'} (.txt)
                </a>
              ) : (
                <button type="button" className="button" onClick={downloadTranscript}>
                  Download {model.fullTextAvailable ? 'transcript' : 'passages'} (.txt)
                </button>
              )}
            </div>
            <label className="research-field transcript-search">
              Search transcript
              <input
                type="search"
                value={transcriptQuery}
                placeholder="Search all remarks or a speaker"
                onChange={(event) => {
                  setTranscriptQuery(event.target.value)
                  setTranscriptPage(0)
                  setHighlightId(null)
                  setHighlightTurnId(null)
                  setQuoteFocus(null)
                  setJumpNotice('')
                }}
              />
            </label>
            <Pager
              page={currentTranscriptPage}
              total={filteredTurns.length}
              size={TRANSCRIPT_PAGE_SIZE}
              noun="speaker turns"
              jump
              onPage={(page) => {
                setTranscriptPage(page)
                setHighlightId(null)
                setHighlightTurnId(null)
                setQuoteFocus(null)
                scrollPanel()
              }}
            />
            {filteredTurns
              .slice(
                currentTranscriptPage * TRANSCRIPT_PAGE_SIZE,
                (currentTranscriptPage + 1) * TRANSCRIPT_PAGE_SIZE,
              )
              .map((turn) => (
                <Turn
                  key={turn.id}
                  turn={turn}
                  elementId={`${instanceId}-${turn.id}`}
                  selected={turn.id === highlightedTurn?.id}
                  highlightStart={turn.id === highlightedTurn?.id ? quoteFocus?.offset ?? model.sentenceOffsets.get(highlightId || '')?.start ?? null : undefined}
                  highlight={
                    turn.id === highlightedTurn?.id
                      ? quoteFocus?.text || highlightedSentence?.text
                      : transcriptQuery.trim()
                  }
                />
              ))}
            {!filteredTurns.length && (
              <EmptyState>
                No speaker turns match this search.{' '}
                <button
                  type="button"
                  className="transcript-text-button"
                  onClick={() => setTranscriptQuery('')}
                >
                  Clear search
                </button>
              </EmptyState>
            )}
            <Pager
              page={currentTranscriptPage}
              total={filteredTurns.length}
              size={TRANSCRIPT_PAGE_SIZE}
              noun="speaker turns"
              jump
              onPage={(page) => {
                setTranscriptPage(page)
                setHighlightId(null)
                setHighlightTurnId(null)
                setQuoteFocus(null)
                scrollPanel()
              }}
            />
          </>
        ) : context ? (
          <div className="transcript-context-reader" ref={contextRef} tabIndex={-1}>
            <div className="transcript-context-heading">
              <button type="button" className="transcript-back" onClick={backToEvidence}>
                ← Back to {tab === 'themes' ? theme?.name || 'themes' : 'negative language'}
              </button>
              <h3>{context.exchange ? 'The full question and response' : 'The full remarks'}</h3>
              <p>
                {context.exchange
                  ? 'All turns in this exchange are shown in call order, regardless of sentiment.'
                  : 'The complete speaker turn surrounding the selected passage.'}
              </p>
            </div>
            {context.turns.length ? (
              context.turns.map((turn) => (
                <Turn
                  key={turn.id}
                  turn={turn}
                  highlightStart={model.sentenceOffsets.get(String(contextSentence?.id))?.start ?? null}
                  highlight={
                    turn.sentence_ids.some((id) => String(id) === String(contextSentence?.id))
                      ? contextSentence?.text
                      : undefined
                  }
                />
              ))
            ) : (
              <p>{contextSentence?.text}</p>
            )}
            {context.exchange && !context.exchange.answered && (
              <p className="transcript-gap">
                No identifiable management response is present in this exchange.
              </p>
            )}
            <div className="transcript-card-actions">
              <button type="button" className="button" onClick={backToEvidence}>
                ← Back to results
              </button>
              {contextSentence && (
                <button
                  type="button"
                  className="transcript-text-button"
                  id={`${instanceId}-context-source`}
                  onClick={() => jumpToSentence(contextSentence.id)}
                >
                  View this passage in the full transcript
                </button>
              )}
            </div>
          </div>
        ) : (
          <>
            {tab === 'negative' && (
              <div className="transcript-negative-controls">
                <p>
                  Negative wording is a reading aid, not a measure of business risk. An analyst’s
                  question is not management’s conclusion. Responses remain attached even when their
                  wording is positive or neutral.
                </p>
                <div className="research-fields">
                  <label className="research-field">
                    Whose wording?
                    <select
                      value={negativeScope}
                      onChange={(event) => {
                        setNegativeScope(event.target.value)
                        setNegativePage(0)
                      }}
                    >
                      <option value="management">
                        Management ({negativeSentences(result, 'management').length})
                      </option>
                      <option value="analyst">
                        Analyst questions ({negativeSentences(result, 'analyst').length})
                      </option>
                      <option value="all">
                        All speakers ({negativeSentences(result, 'all').length})
                      </option>
                    </select>
                  </label>
                  <label className="research-field">
                    Search negative passages
                    <input
                      type="search"
                      placeholder="Topic or speaker"
                      value={negativeQuery}
                      onChange={(event) => {
                        setNegativeQuery(event.target.value)
                        setNegativePage(0)
                      }}
                    />
                  </label>
                </div>
              </div>
            )}
            <div className={tab === 'themes' ? 'transcript-theme-layout' : undefined}>
              {tab === 'themes' && (
                <nav className="transcript-theme-nav" aria-label="Business themes">
                  <span className="transcript-nav-label">Choose a theme</span>
                  {result.themes.map((item, index) => (
                    <button
                      type="button"
                      key={item.name}
                      aria-pressed={theme === item}
                      onClick={() => {
                        setThemeIndex(index)
                        setThemePage(0)
                        scrollPanel()
                      }}
                    >
                      <span>{item.name}</span>
                      <small>{item.mentions}</small>
                    </button>
                  ))}
                </nav>
              )}
              <div className="transcript-evidence-list">
                <div className="transcript-section-heading">
                  <div>
                    <h3>{heading}</h3>
                    <p>
                      {matches.length} matching {matches.length === 1 ? 'passage' : 'passages'} in{' '}
                      {groups.length} {groups.length === 1 ? 'discussion' : 'discussions'}. The quoted
                      summaries pair the analyst’s question with management’s replies and nearby qualifiers.
                    </p>
                  </div>
                  <label className="research-field transcript-discussion-order">Discussion order<select value={discussionOrder} onChange={(event) => { setDiscussionOrder(event.target.value); setThemePage(0); setNegativePage(0) }}><option value="questions">Questions & answers first</option><option value="call">Original call order</option></select></label>
                </div>
                <Pager
                  page={currentEvidencePage}
                  total={groups.length}
                  size={EVIDENCE_PAGE_SIZE}
                  noun="discussions"
                  onPage={evidencePage}
                />
                {groups
                  .slice(
                    currentEvidencePage * EVIDENCE_PAGE_SIZE,
                    (currentEvidencePage + 1) * EVIDENCE_PAGE_SIZE,
                  )
                  .map(evidenceCard)}
                {!groups.length && (
                  <EmptyState>
                    {tab === 'negative'
                      ? 'No negative passages match this selection. Try all speakers or clear the search.'
                      : 'No theme passages were identified. The full transcript is available in the next tab.'}
                  </EmptyState>
                )}
                <Pager
                  page={currentEvidencePage}
                  total={groups.length}
                  size={EVIDENCE_PAGE_SIZE}
                  noun="discussions"
                  onPage={evidencePage}
                />
              </div>
            </div>
          </>
        )}
      </div>
      <details className="transcript-method-details">
        <summary>Speaker tone and analysis method</summary>
        <p>{result.summary}</p>
        <div className="research-table-wrap">
          <table className="research-table">
            <thead>
              <tr>
                <th>Speaker</th>
                <th>Role</th>
                <th>Positive</th>
                <th>Negative</th>
                <th>Neutral</th>
              </tr>
            </thead>
            <tbody>
              {result.speakers.map((speaker, index) => (
                <tr key={`${speaker.name}-${index}`}>
                  <td>{speaker.name}</td>
                  <td>{roleLabel(speaker.role)}</td>
                  <td>{speaker.sentiment.positive}</td>
                  <td>{speaker.sentiment.negative}</td>
                  <td>{speaker.sentiment.neutral}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p>{result.method}</p>
        <ul>
          {result.limitations.map((limitation) => (
            <li key={limitation}>{limitation}</li>
          ))}
        </ul>
      </details>
    </section>
  )
}
