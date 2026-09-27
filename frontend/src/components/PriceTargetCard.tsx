import { useId, useRef, useState } from 'react'
import type { EvidenceRef, Namespace, ValuationBlock, ValuationInput, ValuationScenarioName } from '../types'
import { buildPriceTargetModel, valuationMethodView, valuationMethodLabel, targetCitationRows, targetInputDisplay, targetInputLabel, targetNumber, targetPrice, targetProseParts, targetSourceIds, targetSourceUrl, targetTimeExplanation, targetTodayCalculation, type PriceTargetSource, type RecordedPriceTarget } from './priceTargetModel'
import ValuationResearchContext, { CurrentEarningsEvidence } from './ValuationResearchContext'
import ComparableAnalysis from './ComparableAnalysis'
import './price-target.css'

type Props = {
  valuation?: ValuationBlock | null
  futureTarget?: RecordedPriceTarget | null
  horizon?: string | null
  asOf?: string | null
  ticker?: string | null
  namespace?: Namespace
  sources?: PriceTargetSource[]
  citationGaps?: string[]
  loading?: boolean
  onOpenSource?: (source: EvidenceRef) => void | Promise<void>
}
const scenarioLabel = (value: string) => value[0].toUpperCase() + value.slice(1)
const cleanLabel = (value: string) => value.replaceAll('_', ' ')
const displayDate = (value?: string | null) => value && !Number.isNaN(Date.parse(value)) ? new Date(value).toLocaleDateString() : value || 'Not recorded'
const shown = (value: unknown): string => value == null ? 'Not recorded' : typeof value === 'object' ? Object.entries(value as Record<string, unknown>).map(([key, item]) => `${cleanLabel(key)}: ${shown(item)}`).join(' · ') : String(value)

export default function PriceTargetCard({ valuation, futureTarget, horizon, asOf, ticker, namespace, sources = [], citationGaps = [], loading = false, onOpenSource }: Props) {
  const prefix = useId()
  const sourceDetails = useRef<HTMLDetailsElement>(null)
  const [scenario, setScenario] = useState<ValuationScenarioName>('base')
  const [activeCitation, setActiveCitation] = useState<{ id: string; locator?: string } | null>(null)
  const [methodName, setMethodName] = useState<string | null>(null)
  const displayedValuation = valuationMethodView(valuation, methodName)
  const model = buildPriceTargetModel(displayedValuation, { futureTarget, horizon, asOf })
  const current = model.scenarios.find((item) => item.name === scenario)!
  const calculation = current.calculation
  const baseOnly = scenario === 'base' && !calculation
  const inputs = calculation?.inputs ?? (baseOnly ? model.method?.inputs ?? [] : [])
  const formula = calculation?.formula ?? (baseOnly ? model.method?.formula : null)
  const steps = calculation?.steps ?? (baseOnly ? model.method?.steps ?? [] : [])
  const intermediate = calculation?.intermediate_results ?? (baseOnly ? model.method?.intermediate_results ?? {} : {})
  const retainedSources = [...sources, ...(model.researchContext?.sources ?? []).filter((source) => !sources.some((saved) => (saved.id ?? saved.source_ref) === (source.id ?? source.source_ref)))]
  const sourceRows = targetCitationRows(model.sourceIds, [model.rationale, model.method, valuation?.sensitivities, model.missing, citationGaps, model.researchContext], retainedSources)
  const impliedToday = model.researchContext?.implied_today
  const todayBase = impliedToday?.status === 'unavailable' ? null : targetNumber(impliedToday?.scenarios?.base)
  const todayScenario = impliedToday?.status === 'unavailable' ? null : targetNumber(impliedToday?.scenarios?.[scenario])
  const todayCalculation = targetTodayCalculation(model.researchContext, scenario, calculation)
  const timeExplanation = targetTimeExplanation(calculation, model.horizon)
  function openCitation(id: string, number: number, locator?: string) {
    setActiveCitation({ id, locator })
    if (sourceDetails.current) sourceDetails.current.open = true
    requestAnimationFrame(() => { const target = document.getElementById(`${prefix}-source-${number}`); target?.focus({ preventScroll: true }); target?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }) })
  }
  function prose(value: string) {
    return targetProseParts(value).map((part, index) => {
      if (!part.sourceId) return part.text
      const row = sourceRows.find((item) => item.id === part.sourceId)
      if (!row?.source) return <span key={index} title={part.original}>[Source unavailable{part.locator ? ` · ${part.locator}` : ''}]</span>
      return <span className="price-target-citations" key={index}><button type="button" title={[row.source.title, part.locator].filter(Boolean).join(' · ')} aria-label={`Open valuation source ${row.number}${part.locator ? `, ${part.locator}` : ''}`} onClick={() => openCitation(row.id, row.number, part.locator)}>[{row.number}]</button></span>
    })
  }
  function sourceLinks(refs: unknown) {
    const ids = targetSourceIds(refs)
    if (!ids.length) return null
    return <span className="price-target-citations">{ids.map((id) => {
      const item = sourceRows.find((item) => item.id === id)
      if (!item) return null
      return <button type="button" key={id} aria-label={`Open valuation source ${item.number}`} onClick={() => openCitation(id, item.number)}>[{item.number}]</button>
    })}</span>
  }
  function assumption(input: ValuationInput, index: number) {
    const display = targetInputDisplay(input.key, input.value)
    return <div className="price-target-input" key={`${input.key}-${index}`}>
      <div><strong>{targetInputLabel(input.key ?? 'Input')}</strong><span className={`price-target-input-kind is-${input.kind ?? 'unknown'}`}>{input.kind === 'fact' ? 'Sourced fact' : input.kind === 'assumption' ? 'Assumption' : 'Input type not recorded'}</span></div>
      <p><b>{display.value}</b>{input.unit && !display.unitHandled && <span> {input.unit}</span>}{input.currency && input.unit !== input.currency && <span> · {input.currency}</span>}{input.period && <span> · {input.period}</span>}{sourceLinks(input.source_refs)}</p>
      {input.rationale && <p className="price-target-input-reason">{prose(input.rationale)}</p>}
    </div>
  }
  const computing = loading && !model.base
  return <section className={`price-target-card price-target-${model.status}`} aria-labelledby={`${prefix}-title`}>
    <div className="price-target-heading"><div><span className="price-target-kicker">VALUATION</span><h2 id={`${prefix}-title`}>{computing ? 'Calculating the price target' : model.base ? 'Reasoned price target' : 'Price target still needs evidence'}</h2></div><span className="price-target-status">{computing ? 'In progress' : model.status === 'complete' ? 'Calculated from recorded inputs' : model.status === 'legacy' ? 'Earlier target · calculation not retained' : 'Assessment incomplete'}</span></div>
    {computing ? <p className="price-target-note" role="status">The current review is calculating a supported target and its assumptions. The result will appear here when the assessment is ready.</p> : <div className="price-target-summary">{todayBase && <div><span>Implied value today · base</span><strong>{targetPrice(todayBase, model.currency)}</strong></div>}<div><span>Base target{ticker ? ` · ${ticker}` : ''}</span><strong>{model.base ? targetPrice(model.base, model.currency) : 'Not yet supported'}</strong></div><div><span>Holding horizon</span><b>{model.horizon ?? 'Not recorded'}</b></div><div><span>Evidence as of</span><b>{displayDate(model.asOf)}</b></div></div>}
    {todayBase && <p className="price-target-note">The implied value today applies the scenario’s multiple to the dated reported baseline. The future target uses the stated forecast. This is a valuation estimate, not a live share-price quote.</p>}
    {model.rationale && <p className="price-target-rationale">{model.method?.name !== valuation?.selected_method && <strong>Saved decision rationale: </strong>}{prose(model.rationale)}{sourceLinks(model.method?.source_refs)}</p>}
    {model.method?.name && <p className="price-target-method">Method: {valuationMethodLabel(model.method.name)}</p>}
    {(valuation?.methods?.length ?? 0) > 1 && <label className="price-target-method-select">Valuation view <select value={model.method?.name ?? ''} onChange={(event) => setMethodName(event.target.value)}>{valuation?.methods?.map((method) => <option key={method.name} value={method.name} disabled={!method.supported || method.status !== 'complete'}>{valuationMethodLabel(method.name)}{method.name === valuation.selected_method ? ' · selected in decision' : ''}{!method.supported ? ' · evidence needed' : ''}</option>)}</select></label>}
    {model.scenarios.some((item) => item.value) && <div className="price-target-scenarios" role="group" aria-label="Price target scenarios">{model.scenarios.map((item) => <button type="button" key={item.name} aria-pressed={scenario === item.name} onClick={() => setScenario(item.name)}><span>{scenarioLabel(item.name)}</span><strong>{targetPrice(item.value, model.currency)}</strong><small>{scenario === item.name ? 'Calculation shown below' : 'View calculation'}</small></button>)}</div>}
    {model.base && <div className="price-target-bridge"><div className="price-target-bridge-heading"><h3>{scenarioLabel(scenario)} calculation</h3>{formula && <span>{prose(formula)}</span>}</div>{todayScenario && <div className="price-target-today"><span>Implied value today · {scenario}</span><strong>{targetPrice(todayScenario, model.currency)}</strong>{impliedToday?.earnings_basis && <p>{prose(impliedToday.earnings_basis)}{impliedToday.period_end ? ` · Earnings through ${impliedToday.period_end}` : ''}</p>}{(todayCalculation || impliedToday?.formula) && <p>{prose(todayCalculation || impliedToday?.formula || '')}{sourceLinks(impliedToday?.source_refs)}</p>}{impliedToday?.coverage_note && <p>{prose(impliedToday.coverage_note)}</p>}{model.method?.name === 'eps_multiple' && <CurrentEarningsEvidence earnings={model.researchContext?.current_earnings} sourceLinks={sourceLinks} />}</div>}{formula ? <><p className="price-target-equation">{Object.keys(intermediate).filter((key) => ['adjusted_baseline_eps', 'annual_eps_growth', 'forecast_diluted_eps', 'exit_pe', 'enterprise_value', 'equity_value', 'diluted_shares', 'nav_per_unit', 'horizon_months', 'forecast_years', 'baseline_metric', 'annual_metric_growth', 'forecast_metric', 'exit_multiple', 'equity_bridge'].includes(key)).map((key) => <span key={key}>{targetInputLabel(key)} <b>{targetInputDisplay(key, intermediate[key]).value}</b></span>)}{current.value && <span>Future price per share <b>{targetPrice(current.value, model.currency)}</b></span>}</p>{timeExplanation && <p className="price-target-time-explanation">{timeExplanation}</p>}{steps.length > 0 && <ol>{steps.map((step, index) => <li key={index}>{prose(step)}</li>)}</ol>}</> : <p className="price-target-note">The calculation for this {scenario} scenario was not retained. Its assumptions cannot be reconstructed from the target price.</p>}
      {typeof intermediate.forecast_span_rationale === 'string' && <p className="price-target-note">{prose(intermediate.forecast_span_rationale)}</p>}
      {typeof intermediate.historical_baseline_caveat === 'string' && <p className="price-target-note">{prose(intermediate.historical_baseline_caveat)}</p>}
      {inputs.length > 0 && <details className="price-target-assumptions"><summary>Inputs and assumptions <span>{inputs.length}</span></summary><div>{inputs.map(assumption)}</div></details>}
      {Object.keys(intermediate).length > 0 && <details className="price-target-intermediate"><summary>Full calculation bridge</summary><dl>{Object.entries(intermediate).filter(([key]) => !['implied_today', 'comparable_analysis'].includes(key)).map(([key, value]) => <div key={key}><dt>{cleanLabel(key)}</dt><dd>{prose(shown(value))}</dd></div>)}</dl></details>}
    </div>}
    <ComparableAnalysis value={model.method?.intermediate_results?.comparable_analysis} sourceLinks={sourceLinks} />
    {model.researchContext && <ValuationResearchContext context={model.researchContext} ticker={ticker} namespace={namespace} sourceLinks={sourceLinks} prose={prose} />}
    {!!valuation?.sensitivities?.length && <details className="price-target-sensitivity"><summary>What changes the target? <span>{valuation.sensitivities.length} sensitivity checks</span></summary><ul>{valuation.sensitivities.map((item, index) => <li key={index}>{prose(shown(item))}</li>)}</ul></details>}
    {!computing && (model.missing.length > 0 || citationGaps.length > 0) && <details className="price-target-gaps" open={!model.base}><summary>What is needed to finish the assessment</summary><ul>{[...model.missing, ...citationGaps].map((gap, index) => <li key={index}>{prose(gap)}</li>)}</ul></details>}
    {sourceRows.length > 0 && <details className="price-target-sources" ref={sourceDetails}><summary>Valuation sources <span>{sourceRows.length}</span></summary><ol>{sourceRows.map(({ id, number, source, locators }) => {
      const url = targetSourceUrl(source?.url)
      return <li id={`${prefix}-source-${number}`} tabIndex={-1} key={id}><span>[{number}]</span><div>{source ? <>{url ? <a href={url} target="_blank" rel="noreferrer">{source.title || 'Retained source'} ↗</a> : <strong>{source.title || 'Retained source'}</strong>}<p>{[source.published_at || source.publishedAt || source.as_of, locators.join('; '), source.source_version ? `Version ${source.source_version}` : source.version ? `Version ${source.version}` : ''].filter(Boolean).join(' · ')}</p>{(source.quote || source.excerpt) && <blockquote>{source.quote || source.excerpt}</blockquote>}{onOpenSource && <button type="button" onClick={() => void onOpenSource({ ...source, locator: activeCitation?.id === id && activeCitation.locator ? activeCitation.locator : source.locator })}>Open retained source</button>}</> : <p>The source referenced by this valuation is not available in this saved evidence packet.</p>}</div></li>
    })}</ol></details>}
    {model.base && <p className="price-target-footnote">These targets depend on the stated assumptions. Bear, base and bull are scenarios, not assigned probabilities.</p>}
  </section>
}
