import { useEffect, useRef, useState } from 'react'
import { MemoryDetail, MemoryExplorer } from '../panels/Memory'
import ResearchNotice from './ResearchNotice'
import { calculateExample, example, questions, reviewThreshold, scenarios, syntheticGraph, syntheticNotes } from './syntheticData'
import '../styles.css'
import './demo.css'

const sections = ['Research', 'Watchlist', 'Portfolio', 'Congress', 'Strategy testing', 'Memory'] as const
type Section = typeof sections[number]
const steps = ['Earnings', 'Five questions', 'Pricing', 'Decision'] as const
const repositoryUrl = 'https://github.com/johnson-lee-v0/Equity-Research'
const money = (value: number) => value.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 })
const percent = (value: number) => `${value > 0 ? '+' : ''}${value.toFixed(1)}%`
const noAction = () => {}

function ExternalLink({ href, children }: { href: string; children: React.ReactNode }) {
  return <a href={href} target="_blank" rel="noopener noreferrer">{children} ↗</a>
}

export function SourceDirectory() {
  return <section className="demo-source-directory" aria-label="Primary sources and news links">
    <h2>Explore original sources</h2><p>Open filings and publishers on their own sites. Their content and access terms remain theirs; no news feeds, articles or market observations are reproduced in this demo.</p>
    <div className="demo-source-row"><ExternalLink href="https://www.sec.gov/edgar/search/">SEC filings</ExternalLink><ExternalLink href="https://investor.atmeta.com/">Meta investor relations</ExternalLink><ExternalLink href="https://www.marketwatch.com/">MarketWatch</ExternalLink><ExternalLink href="https://www.bloomberg.com/markets">Bloomberg Markets</ExternalLink><ExternalLink href="https://www.wsj.com/finance">The Wall Street Journal</ExternalLink></div>
  </section>
}

export function EarningsExample({ onNext = noAction }: { onNext?: () => void }) {
  const latest = example.quarters.at(-1)!
  return <div className="demo-step-body"><span className="eyebrow">Invented figures · arbitrary currency units</span><h3>Growth and cash can tell different stories</h3>
    <p>The fictional workshop expands its sales while investing in equipment. Every number below was invented for this lesson; no real company, filing or earnings call is represented.</p>
    <div className="demo-stats"><div><span>Final-quarter revenue</span><strong>{latest.revenue}</strong><small>Invented example</small></div><div><span>Operating profit</span><strong>{latest.operatingProfit}</strong><small>{(latest.operatingProfit / latest.revenue * 100).toFixed(1)}% simplified margin</small></div><div><span>Cash less capital spending</span><strong>{latest.operatingCash - latest.capitalSpending}</strong><small>{latest.operatingCash} − {latest.capitalSpending}, simplified</small></div></div>
    <figure className="demo-teaching-chart"><figcaption>Invented quarterly revenue · not market history</figcaption><div className="demo-teaching-bars">{example.quarters.map(row => <div key={row.period}><div className="demo-teaching-bar" style={{ height: `${row.revenue}px` }}><span>{row.revenue}</span></div><small>{row.period}</small></div>)}</div></figure>
    <p className="demo-table-caption">Original fictional teaching inputs</p><p className="demo-table-hint">Scroll the table horizontally to see every column.</p><div className="demo-table-wrap" role="region" aria-label="Fictional quarterly figures, scrollable table" tabIndex={0}><table aria-label="Original fictional teaching inputs"><thead><tr><th>Quarter</th><th>Revenue</th><th>Operating profit</th><th>Operating cash</th><th>Capital spending</th></tr></thead><tbody>{example.quarters.map(row => <tr key={row.period}><th scope="row">{row.period}</th><td>{row.revenue}</td><td>{row.operatingProfit}</td><td>{row.operatingCash}</td><td>{row.capitalSpending}</td></tr>)}</tbody></table></div>
    <p className="demo-muted">A real research review must reconcile definitions and reporting periods with original filings. This simplified example is not an accounting model.</p><button type="button" className="demo-primary" onClick={onNext}>Review the five questions →</button>
  </div>
}

export function ScenarioTable() {
  return <><p className="demo-table-caption">Three teaching scenarios · invented inputs, not a probability model</p><p className="demo-table-hint">Scroll the table horizontally to see every column.</p><div className="demo-table-wrap" role="region" aria-label="Fictional scenarios, scrollable table" tabIndex={0}><table aria-label="Three teaching scenarios"><thead><tr><th>Example</th><th>EPS growth assumption</th><th>P/E assumption</th><th>One-year calculation</th><th>Change from invented $50 reference</th></tr></thead><tbody>{scenarios.map(row => <tr key={row.name}><th scope="row">{row.name}</th><td>{percent(row.growth)}</td><td>{row.multiple}×</td><td>{money(row.future)}</td><td>{percent(row.changePercent)}</td></tr>)}</tbody></table></div></>
}

export function PricingExample() {
  const [growth, setGrowth] = useState<number>(example.defaultGrowthPercent)
  const [multiple, setMultiple] = useState<number>(example.defaultMultiple)
  const result = calculateExample(growth, multiple)!
  return <section className="demo-pricing" aria-label="Fictional valuation sensitivity"><ResearchNotice scenarios />
    <h2>Try an assumption, inspect the arithmetic</h2><p>Invented annual EPS: {money(example.annualEps)}. Invented reference price: {money(example.referencePrice)}. Neither is a market observation. This teaching model applies growth once over one year.</p>
    <div className="demo-controls"><label>Annual EPS growth assumption: {growth}%<input aria-label="Annual EPS growth assumption" type="range" min="-80" max="50" value={growth} onChange={event => setGrowth(Number(event.target.value))} /></label><label>P/E multiple assumption<input aria-label="P/E multiple assumption" type="number" min="1" max="40" step="1" value={multiple} onChange={event => setMultiple(Math.max(1, Math.min(40, Number(event.target.value) || 1)))} /></label></div>
    <div className="demo-stats" aria-live="polite"><div><span>Current EPS × multiple</span><strong>{money(result.today)}</strong><small>No growth step</small></div><div><span>One-year calculation</span><strong>{money(result.future)}</strong><small>Invented EPS × (1 + growth) × multiple</small></div><div><span>Change from invented $50</span><strong>{percent(result.changePercent)}</strong><small>Arithmetic comparison, not a return forecast</small></div></div>
    <p className="demo-muted">Formula: {money(example.annualEps)} × (1 + {growth}%) × {multiple} = {money(result.future)}. No dividends, fees, taxes, debt changes or dilution are modeled. Changing these controls does not change the authored examples below.</p><ScenarioTable />
  </section>
}

export function DecisionExample() {
  return <><ResearchNotice scenarios /><span className="demo-badge">Fictional decision exercise</span><h3>Ask what evidence would change your view</h3><p>The central teaching calculation is {money(scenarios[1].future)} versus an invented {money(example.referencePrice)} reference. That small difference does not establish an investment opportunity.</p><ul><li>Check whether higher sales become durable cash generation.</li><li>Test whether a lower multiple could outweigh earnings growth.</li><li>Identify what the simple model omits before forming any real-world conclusion.</li></ul><p className="demo-muted">This exercise creates no recommendation, trade, personal allocation or record of actual investment performance.</p></>
}

function MemoryExample() {
  const [selected, setSelected] = useState('cedar')
  const reader = useRef<HTMLDivElement>(null)
  const note = syntheticNotes.find(item => item.id === selected)!
  return <><div className="demo-section-heading"><span className="eyebrow">Fictional teaching notebook</span><h1>Follow the assumptions and questions</h1><p>These authored notes demonstrate a connected research graph. They are not live agent findings, reported company facts or personal records.</p></div><div className="memory-workspace"><MemoryExplorer graph={syntheticGraph} selectedId={selected} onSelect={setSelected} onRead={() => { reader.current?.scrollIntoView({ block: 'start' }); reader.current?.querySelector('aside')?.focus({ preventScroll: true }) }} /><div ref={reader}><MemoryDetail note={note} node={note} loading={false} error="" onSelect={setSelected} onRetry={noAction} /></div></div></>
}

export default function SyntheticDemo() {
  const [section, setSection] = useState<Section>('Research')
  const [openCase, setOpenCase] = useState(false)
  const [step, setStep] = useState(0)
  const [challenge, setChallenge] = useState(false)
  const caseRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const sync = () => {
      let label: string
      try { label = decodeURIComponent(window.location.hash.slice(1)).toLowerCase() } catch { return }
      const next = sections.find(item => item.toLowerCase().replaceAll(' ', '-') === label)
      if (next) setSection(next)
    }
    sync(); window.addEventListener('hashchange', sync); return () => window.removeEventListener('hashchange', sync)
  }, [])
  function navigate(next: Section) { window.location.hash = next.toLowerCase().replaceAll(' ', '-'); setSection(next) }
  function explore() { setOpenCase(true); setStep(0); navigate('Research'); requestAnimationFrame(() => caseRef.current?.scrollIntoView({ block: 'start' })) }
  return <div className="demo-app">
    <a className="skip-link" href="#demo-main">Skip to content</a>
    <header className="demo-topbar"><a className="demo-brand" href="#research">ResearchCouncil <span>Public demo</span></a><ExternalLink href={repositoryUrl}>Run locally / source</ExternalLink></header>
    <nav className="demo-nav" aria-label="Primary navigation">{sections.map(item => <a key={item} href={`#${item.toLowerCase().replaceAll(' ', '-')}`} aria-current={section === item ? 'page' : undefined}>{item}</a>)}</nav>
    <main id="demo-main" tabIndex={-1}>
      <ResearchNotice />
      <p className="demo-provenance"><strong>Fictional teaching demo · authored October 2, 2026.</strong> Static examples and browser calculations, with no live research, market feed or order execution. Figures and company names in the example are invented; they are not relabeled issuer data.</p>
      {section === 'Research' && <>
        <div className="demo-section-heading"><span className="eyebrow">From a question to a testable assumption</span><h1>Practice the research process</h1><p>Explore a fictional workshop’s finances, change valuation assumptions and connect the open questions in its notebook.</p></div>
        <section className="demo-research-start"><div><span className="eyebrow">Original fictional example</span><h2>Cedar Workshop: is expansion producing cash?</h2><p>Four invented quarters, five research questions and a transparent sensitivity calculation.</p></div><button type="button" className="demo-primary" onClick={explore}>{openCase ? 'Back to the example' : 'Open fictional research'} →</button></section>
        {openCase && <section className="demo-case" ref={caseRef} aria-label="Fictional research walkthrough"><div className="demo-case-heading"><div><span className="eyebrow">Cedar Workshop · entirely fictional</span><h2>Follow the research process</h2></div><span className="demo-badge">No live agent output</span></div>
          <nav className="demo-steps" aria-label="Research process">{steps.map((title, index) => <button type="button" key={title} aria-current={step === index ? 'step' : undefined} onClick={() => setStep(index)}><span>{index + 1}</span>{title}</button>)}</nav>
          {step === 0 && <EarningsExample onNext={() => setStep(1)} />}
          {step === 1 && <div className="demo-step-body"><h3>Five questions to investigate</h3><ol className="demo-questions">{questions.map(item => <li key={item.title}><h4>{item.title}</h4><p>{item.answer}</p></li>)}</ol><button type="button" className="demo-primary" onClick={() => setStep(2)}>Try the calculation →</button></div>}
          {step === 2 && <div className="demo-step-body"><PricingExample /><button type="button" className="demo-primary" onClick={() => setStep(3)}>Review the decision exercise →</button></div>}
          {step === 3 && <div className="demo-step-body"><DecisionExample /><button type="button" className="demo-primary" onClick={() => setChallenge(value => !value)}>{challenge ? 'Hide the counterargument' : 'Reveal the authored counterargument'}</button>{challenge && <p className="demo-notice">More sales may require more spending before customers pay. Improving revenue alone does not prove stronger cash returns. This is prewritten teaching material, not a new model response.</p>}</div>}
        </section>}
        <SourceDirectory />
      </>}
      {section === 'Strategy testing' && <><div className="demo-section-heading"><span className="eyebrow">Fictional sensitivity analysis</span><h1>What changes when assumptions change?</h1><p>No historical backtest or realized performance is shown.</p></div><PricingExample /></>}
      {section === 'Watchlist' && <><div className="demo-section-heading"><span className="eyebrow">Fictional research trigger</span><h1>A threshold starts a review</h1><p>An invented review threshold is {money(reviewThreshold)}: EPS of {money(example.annualEps)} × {example.defaultMultiple} × (1 − {example.reviewDiscountPercent}%). The discount is a teaching assumption.</p></div><ResearchNotice scenarios /><p>The example threshold is not a real security’s price, an order, or a recommendation. A threshold alone cannot establish value or suitability.</p><button type="button" className="demo-primary" onClick={() => navigate('Strategy testing')}>Inspect the assumptions →</button></>}
      {section === 'Portfolio' && <><div className="demo-section-heading"><span className="eyebrow">Portfolio boundary</span><h1>No personal account information is collected here</h1><p>This static demo has no account connection, portfolio upload or position-sizing feature. The separate local app can store information that its operator chooses to enter.</p></div><p>The fictional exercise says nothing about anyone’s actual holdings, conflicts of interest or financial circumstances.</p><ExternalLink href={`${repositoryUrl}#readme`}>Read the local setup and data boundary</ExternalLink></>}
      {section === 'Congress' && <><div className="demo-section-heading"><span className="eyebrow">Original disclosure sources</span><h1>Begin with the filing</h1><p>No transaction records or personal holdings are reproduced here. Publication dates, value ranges and reporting delays need independent review before any inference.</p></div><div className="demo-source-row"><ExternalLink href="https://disclosures-clerk.house.gov/">House disclosure register</ExternalLink><ExternalLink href="https://efdsearch.senate.gov/search/">Senate disclosure register</ExternalLink></div></>}
      {section === 'Memory' && <MemoryExample />}
    </main>
    <footer className="demo-footer"><span>Static fictional demo. Controls stay in this page. No app analytics or account collection; hosting providers may log requests. External links open sites with their own policies.</span><div className="demo-license-links"><a href={`${import.meta.env.BASE_URL}LICENSE.txt`}>MIT license</a><a href={`${import.meta.env.BASE_URL}THIRD_PARTY_NOTICES.txt`}>Third-party notices</a><ExternalLink href={repositoryUrl}>Source & setup</ExternalLink></div></footer>
  </div>
}
