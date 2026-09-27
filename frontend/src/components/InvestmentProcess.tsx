import { earningsReviewHref, earningsReviewLinkLabel, earningsStageLabel, type EarningsReviewReceipt } from './investmentProcessModel'
import './investment-process.css'

export default function InvestmentProcess({ ticker, earnings, questionCount = 0, answeredCount = 0, pricingRecorded = false, decisionRecorded = false, latestEarningsId, latestEarningsStatus, onOpenEarnings }: {
  ticker?: string | null
  earnings?: EarningsReviewReceipt
  questionCount?: number
  answeredCount?: number
  pricingRecorded?: boolean
  decisionRecorded?: boolean
  latestEarningsId?: string
  onOpenEarnings?: () => void
  latestEarningsStatus?: string
}) {
  const stages = [
    { label: 'Ticker', detail: ticker || 'Awaiting company', saved: !!ticker },
    { label: 'Earnings', detail: earningsStageLabel(earnings), saved: ['completed', 'partial', 'saved'].includes(earnings?.status || '') },
    { label: '5 questions', detail: questionCount ? `${Math.min(questionCount, 5)} of 5 saved` : 'Not yet recorded', saved: questionCount >= 5 },
    { label: 'Answers', detail: answeredCount ? `${Math.min(answeredCount, 5)} of 5 saved` : 'Not yet recorded', saved: answeredCount >= 5 },
    { label: 'Pricing', detail: pricingRecorded ? 'Valuation saved' : 'Not yet recorded', saved: pricingRecorded },
    { label: 'Decision', detail: decisionRecorded ? 'Conclusion saved' : 'Not yet recorded', saved: decisionRecorded },
  ]
  return <section className="investment-process" aria-label="Investment process">
    <ol>{stages.map((stage, index) => <li key={stage.label} className={stage.saved ? 'is-saved' : undefined}><span className="investment-process-number" aria-hidden="true">{index + 1}</span><span><strong>{stage.label}</strong><small>{stage.detail}</small></span></li>)}</ol>
    {(earnings || latestEarningsId) && <div className="investment-process-earnings">
      {earnings?.workflow_id && (onOpenEarnings ? <button type="button" className="button" onClick={onOpenEarnings}>Open earnings & materials{earnings.fiscal_period ? ` · ${earnings.fiscal_period}` : ''}</button> : <a className="button" href={earningsReviewHref(earnings.workflow_id)}>{earningsReviewLinkLabel(earnings)}{earnings.fiscal_period ? ` · ${earnings.fiscal_period}` : ''}</a>)}
      {latestEarningsId && latestEarningsId !== earnings?.workflow_id && <a className="button button-subtle" href={earningsReviewHref(latestEarningsId)}>{earningsReviewLinkLabel({ status: latestEarningsStatus }, true)}</a>}
      {earnings?.status === 'collecting' && <p role="status">Collecting the latest earnings materials before answering the investment questions.</p>}
      {earnings?.gaps?.length ? <details><summary>Earnings evidence gaps <span>{earnings.gaps.length}</span></summary><ul>{earnings.gaps.map((gap, index) => <li key={index}>{gap}</li>)}</ul></details> : null}
    </div>}
  </section>
}
