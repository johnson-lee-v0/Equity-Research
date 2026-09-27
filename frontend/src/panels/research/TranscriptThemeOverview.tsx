import type { PlainLanguageBriefs, TranscriptResult } from './DocumentViews'
import type { ReadingModel } from './transcriptContext'
import { discussionAnswerCoverage, transcriptThemeOverview } from './earningsReviewModel'

export default function TranscriptThemeOverview({ result, model, briefs, onSelect }: { result: TranscriptResult; model: ReadingModel; briefs?: PlainLanguageBriefs; onSelect: (index: number, previewGroupId?: string) => void }) {
  const themes = transcriptThemeOverview(result, model, briefs)
  if (!themes.length) return null
  return <section className="transcript-theme-overview" aria-label="All saved business themes">
    <div className="transcript-overview-heading"><h2>All {themes.length} business themes</h2><p>One saved interpretation per theme. Open a theme for every discussion, its replies and source wording. Answer coverage describes the reply, not whether its claims are true.</p></div>
    <div className="transcript-overview-grid">{themes.map(({ theme, index, groups, group, brief, previewBullets }) => {
      const coverage = group ? discussionAnswerCoverage(group, brief?.answer_status) : null
      return <article key={theme.name} className="transcript-overview-card">
        <h3>{theme.name}</h3>
        {brief ? previewBullets.map((bullet, i) => <p key={i}><strong>{bullet.kind === 'question' ? 'Asked:' : bullet.kind === 'answer' ? 'Answered:' : 'Management said:'}</strong> {bullet.summary}</p>) : <p className="transcript-cues">A plain-language preview is not saved. The available source passages remain accessible.</p>}
        {coverage && <p className="transcript-overview-coverage"><strong>{coverage.label}:</strong> {coverage.text}</p>}
        <button type="button" className="transcript-text-button" onClick={() => onSelect(index, group?.id)}>Explore {groups.length} {groups.length === 1 ? 'discussion' : 'discussions'} →</button>
      </article>
    })}</div>
  </section>
}
