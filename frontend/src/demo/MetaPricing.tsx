import { useState } from 'react'
import { ValuationResearchContextView } from '../components/ValuationResearchContext'
import { formatCalendarDate } from '../date'
import ResearchNotice from './ResearchNotice'
import { calculateMetaPrice, metaBaselineFacts, metaEarningsBridge, metaEntry, metaHistoricalContext, metaMarket, metaScenarios, metaTtm, valuationMethods } from './metaValuation'

export const money = (value: number) => value.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 })
const percent = (value: number) => `${value > 0 ? '+' : ''}${value.toFixed(1)}%`
const sourceLinks = (refs: unknown) => Array.isArray(refs) ? refs.filter((url): url is string => typeof url === 'string' && url.startsWith('https://')).map((url, i) => <span key={`${url}-${i}`}> · <a href={url} target="_blank" rel="noreferrer">Source {i + 1} ↗</a></span>) : null

export function MetaScenarioTable() {
  return <><p className="demo-table-hint">Scroll the table horizontally to see every scenario input.</p><div className="demo-table-wrap" role="region" aria-label="META scenarios, scrollable table" tabIndex={0}><table aria-label="META twelve-month scenarios"><thead><tr><th scope="col">Scenario</th><th scope="col">Exit P/E assumption</th><th scope="col">EPS growth assumption</th><th scope="col">Implied today</th><th scope="col">12-month value</th><th scope="col">Versus dated close</th></tr></thead><tbody>{metaScenarios.map(row => <tr key={row.label}><th scope="row">{row.label}<small>{row.rationale}</small></th><td>{row.multiple.toFixed(1)}×</td><td>{percent(row.growthPercent)}</td><td>{money(row.today)}</td><td>{money(row.future)}</td><td>{percent(row.upsidePercent)}</td></tr>)}</tbody></table></div></>
}

export function MetaDecision() {
  const base = metaScenarios.find(row => row.label.toLowerCase() === 'base') ?? metaScenarios[1]
  return <><ResearchNotice scenarios /><span className="demo-badge">Research conclusion · September 26, 2026</span><h3>{metaMarket.price > metaEntry.price ? 'Keep META on watch for price and cash returns' : 'META meets the price trigger; the evidence still needs review'}</h3><p>At the {formatCalendarDate(metaMarket.date)} close of <strong>{money(metaMarket.price)}</strong>, the base scenario’s twelve-month value is <strong>{money(base.future)}</strong> ({percent(base.upsidePercent)}). This is a calculation from reported earnings and explicit assumptions, not management guidance.</p><MetaScenarioTable /><ul><li>Q2 revenue grew 28%, but operating profit fell 8% and free cash flow was $0.784bn after capital spending.</li><li>Muse adds a possible revenue stream; no standalone profit contribution is included in these forecasts.</li><li>Revisit the case when cash returns improve, the valuation changes, or Muse discloses measurable economics.</li></ul><p className="demo-muted">Published scenarios use the defaults below; changes in the pricing tool are your own sensitivity analysis. No personal position size or trade is implied.</p></>
}

export function MetaWatchlist({ onPricing, onResearch }: { onPricing: () => void; onResearch: () => void }) {
  return <article className="demo-research-start"><div><span className="demo-badge">META · derived review threshold</span><h2>Review at {money(metaEntry.price)} or below</h2><p>{metaEntry.assumption}</p><p>Latest sourced close: {money(metaMarket.price)} · {formatCalendarDate(metaMarket.date)}. {metaMarket.price > metaEntry.price ? 'Price is above this review threshold.' : 'Price has reached this review threshold; reassess the evidence before acting.'}</p><small>Also review after the next earnings release or quantified Muse adoption and costs. This is a research trigger, not an order.</small><div className="demo-source-row"><a href={metaMarket.source} target="_blank" rel="noreferrer">Dated market source ↗</a></div></div><div className="demo-action-row"><button type="button" className="demo-primary" onClick={onPricing}>Inspect the calculation →</button><button type="button" className="button" onClick={onResearch}>Review the evidence →</button></div></article>
}

export default function MetaPricing() {
  const first = valuationMethods[0]
  const [method, setMethod] = useState(first.key)
  const selected = valuationMethods.find(item => item.key === method) ?? first
  const [growth, setGrowth] = useState(first.growth)
  const [multiple, setMultiple] = useState(first.multiple ?? 1)
  const result = calculateMetaPrice(method, growth, multiple)
  const peGrowth = method === 'P/E' ? growth : first.growth
  const historical = metaHistoricalContext()
  const history = historical.historical_multiples?.[method]
  const context = { as_of: historical.as_of, historical_multiples: history ? { [method]: history } : {} }
  return <section className="demo-pricing" aria-label="META valuation">
    <ResearchNotice scenarios />
    <div className="demo-valuation-intro"><span className="eyebrow">Comparable-multiple method</span><h3>Choose the multiple. Check the evidence.</h3><p>Start with the valuation basis and the price paid for each unit of reported earnings, sales or equity. This public case compares META with its own historical ratios; <strong>no verified peer-company comparables are included</strong>. Historical averages do not automatically become a target.</p></div>
    <div className="demo-stats"><div><span>META · {formatCalendarDate(metaMarket.date)} close</span><strong>{money(metaMarket.price)}</strong><small>Regular trading session · USD</small></div><div><span>Reported financial period</span><strong>Q2 2026</strong><small>Trailing figures end June 30, 2026</small></div><div><span>Forecast horizon</span><strong>12 months</strong><small>One growth period, exponent 1</small></div></div>
    <div className="demo-source-row"><a href={metaMarket.source} target="_blank" rel="noreferrer">Verify market price ↗</a></div>
    <p className="demo-muted">Reported and market figures are sourced below. The controls change your sensitivity view; the published decision and watchlist keep their stated defaults. Muse has no separate forecast contribution.</p>
    <p className="demo-notice"><strong>Earnings need context</strong>{metaTtm.taxNote}</p>
    <div className="demo-methods" role="group" aria-label="Valuation method">{valuationMethods.map(item => <button type="button" key={item.key} aria-pressed={method === item.key} onClick={() => { setMethod(item.key); setMultiple(item.multiple ?? 1); setGrowth(item.growth) }}>{item.key}</button>)}</div>
    <p>{selected.methodReason}</p>
    {!selected.available ? <p role="status" className="demo-notice">{selected.unavailableReason}</p> : <>
      <div className="demo-multiple-layout"><div className="demo-multiple-control"><label htmlFor="meta-target-multiple">Target multiple assumption · {method}</label><div><input id="meta-target-multiple" type="number" min="0.1" max="80" step="0.1" value={multiple} onChange={e => setMultiple(Math.max(.1, Math.min(80, Number(e.target.value) || .1)))} /><span aria-hidden="true">×</span></div><p>Apply {multiple.toFixed(1)}× to <strong>{selected.label.toLowerCase()}</strong>. The target is your research assumption, not a peer median or management guidance.</p><p className="demo-muted">{selected.baseline?.toLocaleString('en-US', { maximumFractionDigits: 4 })} {selected.unit} · {selected.period}</p></div><section className="demo-multiple-history" aria-label={`META historical ${method} comparison`}><h4>Company multiple history · {method}</h4><ValuationResearchContextView key={method} context={context} sourceLinks={sourceLinks} prose={text => text} /></section></div>
      <div className="demo-stats" aria-live="polite" aria-atomic="true"><div><span>Implied value today · no growth</span><strong>{result.today == null ? 'Unavailable' : money(result.today)}</strong></div><div><span>12-month scenario value</span><strong>{result.future == null ? 'Unavailable' : money(result.future)}</strong></div><div><span>Versus {formatCalendarDate(metaMarket.date)} close</span><strong>{result.upsidePercent == null ? 'Unavailable' : percent(result.upsidePercent)}</strong><small>Price change only · excludes dividends</small></div></div>
      <p className="demo-muted">Today’s implied value uses the reported baseline and selected multiple, with no growth step. The optional 12-month sensitivity currently assumes {growth}% annual growth.</p>
      {result.reason && <p role="status" className="demo-notice">{result.reason}</p>}
      <details className="demo-growth-sensitivity"><summary>Optional: test growth sensitivity ({growth}% assumed)</summary><p>Growth changes the 12-month scenario, not today’s reported-baseline value. It is secondary to the selected multiple and its evidence.</p><div className="demo-controls"><label>Annual {method === 'P/E' ? 'EPS' : method === 'P/S' ? 'revenue' : method === 'EV/EBITDA' ? 'EBITDA' : 'book-equity'} growth assumption: {growth}%<input type="number" min="-30" max="40" step="1" value={growth} onChange={e => setGrowth(Math.max(-30, Math.min(40, Number(e.target.value) || 0)))} /></label></div></details>
      <details><summary>Reported inputs, assumptions and calculation</summary><p><strong>{selected.label} baseline:</strong> {selected.baseline?.toLocaleString('en-US', { maximumFractionDigits: 4 })} {selected.unit} · {selected.period}.</p><p>{selected.formula}</p><p>Forecast baseline = reported baseline × (1 + {growth}%)¹. Today uses no growth; it is not a discounted future target.</p><ul>{selected.assumptions.map(text => <li key={text}>{text}</li>)}</ul><p>Market capitalization and share basis: {metaMarket.sharesBasis}</p><div className="demo-source-row">{selected.sourceUrls.map((url, i) => <a href={url} key={url} target="_blank" rel="noreferrer">Input source {i + 1} ↗</a>)}</div></details>
    </>}
    <details><summary>Published scenarios and watchlist calculation</summary><MetaScenarioTable /><p>Published review threshold: ${metaTtm.dilutedEps.toFixed(2)} trailing EPS × 26 × (1 − {metaEntry.marginOfSafetyPercent}%) = <strong>{money(metaEntry.price)}</strong>. The 20% margin of safety is our research rule, not a company fact. Sensitivity edits do not rewrite it.</p><p>These scenarios use GAAP earnings, including reported unusual items. Growth and multiple changes are assumptions, not a probability distribution or promised return.</p></details>
    <details><summary>Audit every financial input and source</summary><div className="demo-table-wrap"><table><thead><tr><th>Input</th><th>Value and date</th><th>Basis and source</th></tr></thead><tbody>{metaBaselineFacts.map(fact => <tr key={fact.label}><th scope="row">{fact.label}</th><td>{fact.value.toLocaleString('en-US', { maximumFractionDigits: 6 })} {fact.unit}<small>{fact.period}</small></td><td>{fact.basis}{sourceLinks(fact.sourceUrls)}</td></tr>)}</tbody></table></div></details>
    <details><summary>Optional FY2026 earnings bridge · reported versus projected</summary><ValuationResearchContextView context={{ earnings_bridge: metaEarningsBridge(peGrowth) }} sourceLinks={sourceLinks} prose={text => text} /></details>
  </section>
}
