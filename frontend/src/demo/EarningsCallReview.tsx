import { useState } from 'react'
import { callDiscussions, callRisks, earningsCall, type MetaCallDiscussion } from './metaEarningsCall'
import { metaThemeContexts } from './metaThemeContext'
import { metaTrendSeries } from './metaTrends'
import EarningsTrends, { CapexGuidance } from '../panels/research/EarningsTrends'
import { metaCapexGuidance } from './metaTrends'
import { formatCalendarDate } from '../date'

export function CallDiscussion({ item }: { item: MetaCallDiscussion }) {
  return <li><h4>{item.title}</h4>
    <p><strong>Asked:</strong> {item.question}</p>
    <p><strong>Answered:</strong> {item.answer}</p>
    <p className="demo-call-implication"><strong>Still unclear:</strong> {item.stillUnclear}</p>
    <details><summary>Speaker context and original wording</summary>
      <p><strong>Question:</strong> {item.questionSpeaker} · transcript page {item.questionPage}</p>
      <p><strong>Answer:</strong> {item.answerSpeaker} · transcript page {item.answerPage}</p>
      {item.quote && <blockquote><p>“{item.quote.text}”</p><cite>{item.quote.speaker} · <a href={item.quote.sourceUrl} target="_blank" rel="noreferrer">page {item.quote.page} ↗</a></cite></blockquote>}
      <p className="demo-muted">Summaries are paraphrases; “still unclear” is our research follow-up. Read the full exchange for context.</p>
      <div className="demo-source-row"><a href={item.questionSourceUrl} target="_blank" rel="noreferrer">Full question ↗</a><a href={item.sourceUrl} target="_blank" rel="noreferrer">Full answer ↗</a></div>
    </details>
  </li>
}

export default function EarningsCallReview() {
  const [scope, setScope] = useState<'themes' | 'caution'>('themes')
  const [selectedId, setSelectedId] = useState('growth')
  const [selectedMetrics, setSelectedMetrics] = useState<Record<string, string>>({})
  const theme = metaThemeContexts.find(item => item.id === selectedId) ?? metaThemeContexts[0]
  const rows = scope === 'caution' ? callRisks : callDiscussions.filter(item => item.themeId === theme.id)
  const choices = theme.metricIds.flatMap(id => metaTrendSeries.filter(series => series.id === id))
  const selected = choices.find(item => item.id === selectedMetrics[theme.id]) ?? choices[0]
  return <section id="earnings-call" className="demo-call-review" aria-label="META earnings call analysis">
    <div className="demo-section-heading"><span className="eyebrow">Earnings research · {formatCalendarDate(earningsCall.date)}</span><h3>What analysts asked — and what management answered</h3><p>{earningsCall.summary} Select a business theme or inspect cautionary language, then follow each question and answer to its speaker and transcript page.</p><p className="demo-muted">{metaThemeContexts.length} business themes · {earningsCall.questionCount} editorial topics across {earningsCall.analystCount} analyst exchanges. Earnings-call NLP is one component of the local research workflow. This static editorial reading is not a live model run or automated sentiment score. Company disclosures fill the results and outlook sections.</p><div className="demo-source-row"><a href={earningsCall.source} target="_blank" rel="noreferrer">Read the original earnings-call transcript ↗</a><a href="#financials">Compare reported earnings &amp; financials ↓</a></div></div>
    <div className="demo-methods" role="group" aria-label="Call review view"><button type="button" aria-pressed={scope === 'themes'} onClick={() => setScope('themes')}>Business themes</button><button type="button" aria-pressed={scope === 'caution'} onClick={() => setScope('caution')}>Caution and negative language</button></div>
    {scope === 'themes' && <div className="demo-theme-layout">
      <nav className="demo-theme-nav" aria-label="Business themes">{metaThemeContexts.map(item => {
        const count = callDiscussions.filter(row => row.themeId === item.id).length
        return <button type="button" key={item.id} aria-pressed={item.id === theme.id} onClick={() => setSelectedId(item.id)}><strong>{item.title}</strong><span>{count ? `${count} editorial ${count === 1 ? 'topic' : 'topics'}` : 'Company disclosure'}</span></button>
      })}</nav>
      <div className="demo-theme-content"><h4 className="demo-call-group">{theme.title}</h4><p className="demo-theme-takeaway">{theme.takeaway}</p>
        {!!theme.facts?.length && <div className="demo-theme-facts"><span className="eyebrow">{theme.id === 'outlook' ? 'Management outlook' : theme.id === 'consumer' ? 'Since earnings' : 'Company disclosure'}</span>{theme.facts.map(fact => <p key={fact}>{fact}</p>)}{theme.sourceUrl && <a href={theme.sourceUrl} target="_blank" rel="noreferrer">{theme.sourceLabel} ↗</a>}</div>}
        {theme.openQuestion && <p className="demo-call-implication"><strong>Still unclear:</strong> {theme.openQuestion}</p>}
        {rows.length > 0 ? <ul className="demo-call-items">{rows.map(item => <CallDiscussion key={item.id} item={item} />)}</ul> : <p className="demo-muted">This section uses company disclosures; it is not presented as an analyst question.</p>}
        <section className="demo-theme-trend" aria-label={`Numerical context for ${theme.title}`}><h4>Numbers to test this theme</h4><p className="demo-muted">{theme.metricNote}</p>{choices.length > 0 && <EarningsTrends series={selected} choices={choices} onSelect={id => setSelectedMetrics(prior => ({ ...prior, [theme.id]: id }))} />}{theme.id === 'funding' && <CapexGuidance guidance={metaCapexGuidance} />}</section>
      </div>
    </div>}
    {scope === 'caution' && <><p className="demo-muted">Selected uncertainty and caution in the actual Q&A, with management’s response. This is not a sentiment score. Legal and financial outlook risks are also covered under Outlook & risks.</p><ul className="demo-call-items">{rows.map(item => <CallDiscussion key={item.id} item={item} />)}</ul><button type="button" className="button" onClick={() => { setScope('themes'); setSelectedId('outlook') }}>Review outlook & risks →</button></>}
    <details><summary>Analysis coverage and full transcript</summary><p>{earningsCall.coverage} Editorial topics are grouped into business themes; source links preserve their original order and speaker context.</p><ol>{callDiscussions.map(item => <li key={item.id}><a href={item.questionSourceUrl} target="_blank" rel="noreferrer">{item.questionSpeaker} · page {item.questionPage}</a> → {item.title}</li>)}</ol><a href={earningsCall.source} target="_blank" rel="noreferrer">{earningsCall.title} ↗</a></details>
  </section>
}
