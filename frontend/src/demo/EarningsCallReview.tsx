import { callRisks, callThemes, earningsCall } from './metaEarningsCall'
import { formatCalendarDate } from '../date'

export default function EarningsCallReview() {
  function items(rows: typeof callThemes) {
    return <ul className="demo-call-items">{rows.map(item => <li key={item.id}>
      <h4>{item.title}</h4>
      <p><strong>Asked:</strong> {item.question}</p>
      <p><strong>Answered:</strong> {item.answer}</p>
      <p className="demo-call-implication"><strong>Why it matters:</strong> {item.whyItMatters}</p>
      <details><summary>Speaker context and original wording</summary>
        <p><strong>Question:</strong> {item.questionSpeaker} · transcript page {item.questionPage}</p>
        <p><strong>Answer:</strong> {item.answerSpeaker} · transcript page {item.answerPage}</p>
        {item.quote && <blockquote><p>“{item.quote.text}”</p><cite>{item.quote.speaker} · <a href={item.quote.sourceUrl} target="_blank" rel="noreferrer">page {item.quote.page} ↗</a></cite></blockquote>}
        <p className="demo-muted">The summaries above are paraphrases. Any quoted words are a short excerpt; read the full exchange for context.</p>
        <div className="demo-source-row"><a href={item.questionSourceUrl} target="_blank" rel="noreferrer">Full question ↗</a><a href={item.sourceUrl} target="_blank" rel="noreferrer">Full answer ↗</a></div>
      </details>
    </li>)}</ul>
  }
  return <section className="demo-call-review" aria-label="META earnings call analysis">
    <div className="demo-section-heading"><span className="eyebrow">Earnings call · {formatCalendarDate(earningsCall.date)}</span><h3>What analysts asked — and what management answered</h3><p>{earningsCall.summary}</p></div>
    <h4 className="demo-call-group">Themes</h4>{items(callThemes)}
    <h4 className="demo-call-group">Caution and negative language</h4>{items(callRisks)}
    <details><summary>Analysis coverage and full transcript</summary><p>{earningsCall.coverage}</p><a href={earningsCall.source} target="_blank" rel="noreferrer">{earningsCall.title} ↗</a></details>
  </section>
}
