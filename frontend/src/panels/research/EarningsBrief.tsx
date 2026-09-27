import { useState, type ReactNode } from 'react'
import type { TranscriptResult } from './DocumentViews'
import { displayDate } from './shared'
import EarningsTrends, { CapexGuidance } from './EarningsTrends'
import { selectTrendSeries, seriesForArea, type EarningsTrendsResult } from './earningsTrendModel'
import './earnings-brief.css'

type Sentence = TranscriptResult['sentences'][number]
type Position = {
  symbol?: string
  quantity?: number | string
  currency?: string
  status?: string
  observed_at?: string
  account_id?: string
}
type ReviewArea = {
  id: string
  title: string
  theme: string
  match: RegExp
  priority: RegExp
}

const areas: ReviewArea[] = [
  {
    id: 'demand',
    title: 'Demand & growth',
    theme: 'Demand and growth',
    match: /\b(sales|revenue|demand|traffic|orders|renewal|members)\b/i,
    priority: /\b(net sales|total revenue|comparable sales|organic revenue|renewal rate)\b/i,
  },
  {
    id: 'margins',
    title: 'Margins & costs',
    theme: 'Margins and costs',
    match: /\b(margins?|operating profit|costs?|expenses?)\b/i,
    priority: /\b(gross margin|operating margin|operating profit)\b/i,
  },
  {
    id: 'outlook',
    title: 'Outlook',
    theme: 'Guidance and outlook',
    match: /\b(guidance|outlook|expect|forecast|planning|target)\b/i,
    priority: /\b(guidance|outlook|forecast)\b/i,
  },
  {
    id: 'capital',
    title: 'Capital & cash',
    theme: 'Capital and liquidity',
    match: /\b(capital expenditure|capex|cash flow|liquidity|debt|dividend|buyback|repurchase)\b/i,
    priority: /\b(capital expenditure|capex|free cash flow)\b/i,
  },
  {
    id: 'risk',
    title: 'Risks to revisit',
    theme: 'Risk and regulation',
    match: /\b(risk|uncertainty|uncertain|tariff|inflation|headwind|disruption|pressure)\b/i,
    priority: /\b(uncertainty|uncertain|difficult to predict|headwind|disruption)\b/i,
  },
]

const metricMatches: Record<string, RegExp> = {
  renewal_us_canada: /\brenewal\b/i,
  renewal_worldwide: /\brenewal\b/i,
  net_sales_growth: /\bnet sales\b/i,
  paid_members_growth: /\bpaid (household|members)|\bgrowth.{0,50}members|\bmember.{0,20}growth\b/i,
  gross_margin: /\bgross margin\b/i,
  operating_margin: /\boperating margin|operating income\b/i,
  capex: /\bcapital expenditure|capex\b/i,
  capex_cash_ppe: /\bcapital expenditure|capex\b/i,
}

function selectExcerpt(result: TranscriptResult, area: ReviewArea, metricId?: string): Sentence | undefined {
  const themeIds = new Set(
    result.themes
      .find((theme) => theme.name === area.theme)
      ?.evidence.map((item) => String(item.sentence_id)),
  )
  return result.sentences
    .filter(
      (sentence) =>
        sentence.role === 'management' &&
        sentence.text.length >= 65 &&
        !/forward-looking|safe harbor|litigation reform|risks identified from time|not a substitute for|statements involve risks/i.test(
          sentence.text,
        ) &&
        area.match.test(sentence.text),
    )
    .map((sentence, index) => ({
      sentence,
      index,
      score:
        (metricId && metricMatches[metricId]?.test(sentence.text) ? 20 : 0) +
        (area.priority.test(sentence.text) ? 6 : 0) +
        (themeIds.has(String(sentence.id)) ? 2 : 0) +
        (/\d/.test(sentence.text) ? 2 : 0) +
        (/\b(quarter|year-over-year|outlook|planning)\b/i.test(sentence.text) ? 1 : 0),
    }))
    .sort((a, b) => b.score - a.score || a.index - b.index)[0]?.sentence
}

function EarningsReviewRow({ area, result, trends, trendsLoading, onRead }: {
  area: ReviewArea
  result: TranscriptResult
  trends?: EarningsTrendsResult | null
  trendsLoading?: boolean
  onRead: (sentenceId: string | number) => void
}) {
  const [selectedMetric, setSelectedMetric] = useState<string>()
  const relevant = seriesForArea(trends, area.id)
  const choices = area.id === 'outlook' && relevant.some((series) => !series.area_ids.includes('capital'))
    ? relevant.filter((series) => !series.area_ids.includes('capital'))
    : relevant
  const preferredMetric = area.id === 'outlook' ? 'renewal_worldwide' : area.id === 'demand' ? 'net_sales_growth' : undefined
  const selected = selectTrendSeries(choices, selectedMetric, preferredMetric)
  const sentence = selectExcerpt(result, area, selected?.id)
  const following = sentence ? result.sentences[result.sentences.indexOf(sentence) + 1] : undefined
  const adjustment =
    area.id === 'margins' &&
    following?.role === 'management' &&
    following.speaker === sentence?.speaker &&
    /\b(adjusted|excluding|including)\b/i.test(following.text) &&
    /\b(margins?|profits?|rates?)\b/i.test(following.text)
      ? following
      : undefined
  return (
    <article className="earnings-brief-row" id={`earnings-area-${area.id}`}>
      <div className="earnings-brief-commentary">
        <h3>{area.title}</h3>
        {sentence ? (
          <>
            <blockquote>“{sentence.text}”</blockquote>
            {adjustment && <blockquote>“{adjustment.text}”</blockquote>}
            <button className="earnings-quote-link" onClick={() => onRead(sentence.id)}>
              {sentence.speaker || 'Management'} · Read in context <span aria-hidden="true">↗</span>
            </button>
          </>
        ) : <p className="muted-copy">No management excerpt identified. Review the full call for this topic.</p>}
      </div>
      <div className="earnings-brief-trends">
        <EarningsTrends
          series={selected}
          choices={choices}
          onSelect={setSelectedMetric}
          loading={trendsLoading}
          emptyMessage={!trends ? 'Historical figures have not been collected for this saved review yet. Update earnings materials to add sourced trends.' : undefined}
        />
        {area.id === 'capital' && <CapexGuidance guidance={trends?.capex_guidance} />}
      </div>
    </article>
  )
}

function latestPositions(positions: unknown[], ticker: string): Position[] {
  const latest = new Map<string, Position>()
  for (const value of positions) {
    if (!value || typeof value !== 'object') continue
    const position = value as Position
    if (position.symbol && position.symbol.toUpperCase() !== ticker.toUpperCase()) continue
    const key = position.account_id || 'unassigned'
    const previous = latest.get(key)
    if (!previous || (position.observed_at || '') > (previous.observed_at || ''))
      latest.set(key, position)
  }
  return [...latest.values()]
}

export default function EarningsBrief({
  result,
  ticker,
  positions = [],
  onRead,
  action,
  trends,
  trendsLoading = false,
}: {
  result: TranscriptResult
  ticker: string
  positions?: unknown[]
  onRead: (sentenceId: string | number) => void
  action?: ReactNode
  trends?: EarningsTrendsResult | null
  trendsLoading?: boolean
}) {
  const holdings = latestPositions(positions, ticker)
  return (
    <section
      className="research-card earnings-brief"
      id="earnings-shareholder-brief"
      aria-labelledby="earnings-brief-heading"
    >
      <div className="research-card-heading">
        <div>
          <p className="earnings-kicker">Commentary in context</p>
          <h2 id="earnings-brief-heading">What management said. What the numbers show.</h2>
        </div>
        <span className="earnings-brief-label">Call excerpts + historical data</span>
      </div>
      <p className="earnings-brief-intro">
        Compare the call with reported history. Choose a metric to see its figures over time,
        the exact definition and the sources behind each observation. Guidance is marked separately.
      </p>
      <div className="earnings-brief-rows">
        {areas.filter((area) => {
          // Capital guidance already appears alongside its history in Capital & cash.
          const metrics = seriesForArea(trends, area.id)
          return area.id !== 'outlook' || !metrics.length || metrics.some((series) => !series.area_ids.includes('capital'))
        }).map((area) => <EarningsReviewRow key={area.id} area={area} result={result} trends={trends} trendsLoading={trendsLoading} onRead={onRead} />)}
      </div>
      {!!trends?.gaps?.length && <details className="earnings-trends-gaps">
        <summary>Historical data coverage · {trends.gaps.length} {trends.gaps.length === 1 ? 'gap' : 'gaps'}</summary>
        <ul>{trends.gaps.map((gap, index) => <li key={index}>{gap}</li>)}</ul>
      </details>}
      <div className="earnings-position-review">
        <div>
          <h3>Your {ticker} position</h3>
          {holdings.length ? (
            <>
              <p>
                Latest saved observation per account, as retained when this earnings review ran:
              </p>
              <ul className="earnings-position-records">
                {holdings.map((position, index) => (
                  <li key={position.account_id || index}>
                    {position.account_id || 'Account unspecified'} ·{' '}
                    {position.quantity == null || position.quantity === ''
                      ? 'Quantity unavailable'
                      : `${position.quantity} shares`}
                    {position.currency ? ` · ${position.currency}` : ''}
                    {position.observed_at
                      ? ` · ${displayDate(position.observed_at)}`
                      : ' · Date unavailable'}
                    {position.status ? ` · ${position.status.replaceAll('_', ' ')}` : ''}
                  </li>
                ))}
              </ul>
              <p>
                Review current holdings, valuation and portfolio concentration before drawing a
                position-level conclusion.
              </p>
            </>
          ) : (
            <p>
              No {ticker} position was recorded in this workflow’s portfolio snapshot. Your
              quantity, cost basis and portfolio weight are unknown here.
            </p>
          )}
          <p>
            The Research Engine can investigate the reported trends with the saved sources and your
            available portfolio context.
          </p>
        </div>
        {action && <div className="earnings-position-action">{action}</div>}
      </div>
    </section>
  )
}
