import { useState } from 'react'
import { EmptyState } from './shared'
export { TranscriptView } from './TranscriptReader'

export type Sentiment = {
  label: string
  score: number
  positive: number
  negative: number
  neutral: number
  sentences: number
}
export type Evidence = { sentence_id: string | number; text: string; speaker?: string }
export type TranscriptSentence = {
  id: string | number
  text: string
  speaker?: string
  role?: string
  section?: string
  turn_id?: string | null
  exchange_id?: string | null
  statement_kind?: string
  sentiment: {
    label: string
    score: number
    cues: { term: string; polarity: string; negated: boolean }[]
  }
}
export type TranscriptTurn = {
  id: string
  speaker: string
  role: string
  section: string
  text: string
  sentence_ids: (string | number)[]
  exchange_id?: string | null
  statement_kind?: string
}
export type TranscriptExchange = {
  id: string
  question_turn_ids: string[]
  answer_turn_ids: string[]
  context_turn_ids: string[]
  sentence_ids: (string | number)[]
  answered: boolean
}
export type TranscriptReadingContext = {
  version: string
  transcript_text: string
  full_text_available: boolean
  turns: TranscriptTurn[]
  exchanges: TranscriptExchange[]
}
export type PlainLanguageBriefs = {
  status: string
  source_hash?: string
  error?: string
  gaps: string[]
  discussions: Record<string, {
    group_id: string
    answer_status: 'answered' | 'partial' | 'unclear' | 'no_response' | 'not_a_question'
    bullets: { kind: 'question' | 'answer' | 'remark'; summary: string; turn_ids: string[]; quotes: { turn_id: string; speaker: string; quote: string; offset: number }[] }[]
  }>
}
export type TranscriptResult = {
  method: string
  limitations: string[]
  summary: string
  source_hash?: string
  sentiment: Sentiment
  speakers: { name: string; role: string; sentiment: Sentiment }[]
  entities: { name: string; type: string; mentions: number; evidence: Evidence[] }[]
  themes: { name: string; mentions: number; evidence: Evidence[] }[]
  sentences: TranscriptSentence[]
  reading_context?: TranscriptReadingContext
  plain_language?: PlainLanguageBriefs
}
export type ComparisonResult = {
  method: string
  limitations: string[]
  summary: string
  counts: {
    added: number
    removed: number
    changed: number
    unchanged: number
    numeric_only: number
    excluded: number
  }
  changes: {
    id: string
    type: 'added' | 'removed' | 'changed'
    section: string
    before: string
    after: string
    significance: string
    cues: string[]
  }[]
  exclusions: { previous: Record<string, unknown>; current: Record<string, unknown> }
}

export function ComparisonView({ result }: { result: ComparisonResult }) {
  const [filter, setFilter] = useState('all')
  const [section, setSection] = useState('all')
  const sections = [...new Set(result.changes.map((change) => change.section))]
  const changes = result.changes.filter(
    (change) =>
      (filter === 'all' || filter === change.type) &&
      (section === 'all' || section === change.section),
  )
  return (
    <>
      <dl className="document-stats" aria-label="Commentary comparison summary">
        <div>
          <dt>Changed</dt>
          <dd>{result.counts.changed}</dd>
        </div>
        <div>
          <dt>Added</dt>
          <dd>{result.counts.added}</dd>
        </div>
        <div>
          <dt>Removed</dt>
          <dd>{result.counts.removed}</dd>
        </div>
        <div>
          <dt>Number-only edits ignored</dt>
          <dd>{result.counts.numeric_only}</dd>
        </div>
        <div>
          <dt>Other exclusions</dt>
          <dd>{result.counts.excluded}</dd>
        </div>
      </dl>
      <section className="research-card">
        <div className="research-card-heading">
          <h2>Changes in commentary</h2>
          <span className="muted-copy">{changes.length} passages</span>
        </div>
        <div className="research-fields">
          <label className="research-field">
            Change type
            <select value={filter} onChange={(event) => setFilter(event.target.value)}>
              <option value="all">All changes</option>
              <option value="changed">Changed</option>
              <option value="added">Added</option>
              <option value="removed">Removed</option>
            </select>
          </label>
          <label className="research-field">
            Section
            <select value={section} onChange={(event) => setSection(event.target.value)}>
              <option value="all">All narrative sections</option>
              {sections.map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </label>
        </div>
        {changes.map((change) => (
          <article className="research-diff" key={change.id}>
            <div className="research-card-heading">
              <h3>{change.section || 'Commentary'}</h3>
              <span className={`research-pill ${change.type}`}>{change.type}</span>
            </div>
            <div className="research-grid">
              <div>
                <span className="document-passage-label">Previous filing</span>
                <blockquote className="research-evidence">
                  {change.before || 'No corresponding passage.'}
                </blockquote>
              </div>
              <div>
                <span className="document-passage-label">Current filing</span>
                <blockquote className="research-evidence">
                  {change.after || 'Passage removed.'}
                </blockquote>
              </div>
            </div>
            <p>
              {change.significance}
              {change.cues.length ? ` · ${change.cues.join(', ')}` : ''}
            </p>
          </article>
        ))}
        {!changes.length && (
          <EmptyState>
            No commentary changes match this selection. Unchanged language and numeric-only changes
            are omitted.
          </EmptyState>
        )}
      </section>
      <details className="research-card research-details">
        <summary>Exclusion audit</summary>
        <p>
          Financial statements, numeric tables and numeric-only edits are filtered before
          comparison. Narrative passages containing meaningful language changes remain available.
        </p>
        <pre className="research-raw">{JSON.stringify(result.exclusions, null, 2)}</pre>
      </details>
    </>
  )
}
