import { useEffect, useRef, useState } from 'react'
import MarketNews from '../components/MarketNews'
import EarningsTrends from '../panels/research/EarningsTrends'
import ValuationResearchContext from '../components/ValuationResearchContext'
import { MemoryDetail, MemoryExplorer } from '../panels/Memory'
import { capex, demoGraph, demoNotes, demoPrice, illustrativeHistory, META_RELEASE, META_SLIDES, MUSE_RELEASE, latestFinancials, museThesis, METHODS, methodInfo, questions, type DemoMethod } from './demoData'
import '../styles.css'
import '../components/price-target.css'
import './demo.css'

const sections = ['Research', 'Watchlist', 'Portfolio', 'Congress', 'Strategy testing', 'Memory'] as const
type Section = typeof sections[number]
const steps = ['Earnings', 'Five questions', 'Pricing', 'Decision'] as const
const repositoryUrl = 'https://github.com/johnson-lee-v0/Equity-Research'
function Source({ href, label = 'Company source' }: { href: string; label?: string }) { return <a href={href} target="_blank" rel="noreferrer">{label} ↗</a> }
export function MuseCatalyst() {
  return <section className="demo-catalyst" aria-label="Since earnings: Muse">
    <div><span className="eyebrow">Since earnings · September 8, 2026</span><h3>Muse: a new product, an unproven payback</h3><p>Meta’s personal AI agent arrived after the June quarter. Its launch cannot explain Q2 growth.</p></div>
    <div className="demo-catalyst-thesis"><p><strong>Upside to test</strong>{museThesis.positive}</p><p><strong>Risk to test</strong>{museThesis.opposing}</p><p><strong>What would prove it?</strong>{museThesis.evidence}</p></div>
    <span className="demo-muted">Thesis interpretations · no standalone Muse revenue or profit established by the launch announcement.</span>
    <details><summary>Company claims and launch source</summary><p>Meta describes a US rollout on mobile and web, with WhatsApp access, a free service and paid plans. It says each user has a private cloud computer and that conversations and computer data do not feed its advertising systems.</p><Source href={MUSE_RELEASE} label="Muse launch announcement" /></details>
  </section>
}
export function EarningsSnapshot({ onNext }: { onNext: () => void }) {
  return <div className="demo-step-body"><h3>Sales rose. Operating profit fell.</h3><div className="demo-stats"><div><span>Revenue</span><strong>${latestFinancials.revenue}bn</strong><small>+{latestFinancials.revenueGrowthPercent}% year over year</small></div><div><span>Operating margin</span><strong>{latestFinancials.operatingMarginPercent}%</strong><small>{latestFinancials.priorOperatingMarginPercent}% a year earlier</small></div><div><span>Capital spending</span><strong>${latestFinancials.capex}bn</strong><small>Quarter only · includes lease principal</small></div></div>
    <p className="demo-cash-result">Company-defined free cash flow after CapEx: <strong>${latestFinancials.freeCashFlow}bn</strong>.</p>
    <EarningsTrends series={capex} choices={[capex]} onSelect={() => {}} />
    <details><summary>Quarterly calculation and spending outlook</summary><p>Q2 CapEx = ${latestFinancials.cashPpe}bn cash PP&E + ${latestFinancials.financeLeasePrincipal}bn lease principal. The first-half total, ${latestFinancials.yearToDateCapex}bn, is not a quarterly value.</p><p>FY2026 CapEx guidance: $130–145bn, as given July 29. This is management’s forecast, not spending already reported.</p><p>Diluted EPS: ${latestFinancials.dilutedEps}, versus ${latestFinancials.priorDilutedEps}. Operating cash flow: ${latestFinancials.operatingCashFlow}bn.</p><Source href={`${META_SLIDES}#page=15`} label="Open cash-flow reconciliation" /></details>
    <div className="demo-source-row"><Source href={META_RELEASE} label="Q2 earnings release" /><Source href={META_SLIDES} label="Q2 earnings slides" /></div>
    <MuseCatalyst />
    <button type="button" className="demo-primary" onClick={onNext}>Review the five questions →</button>
  </div>
}
function PricingExercise() {
  const [method, setMethod] = useState<DemoMethod>('P/E')
  const [growth, setGrowth] = useState(8)
  const [multiple, setMultiple] = useState(methodInfo['P/E'].multiple)
  const current = demoPrice(method, 0, multiple)
  const future = demoPrice(method, growth, multiple)
  const money = (value: number) => value.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 })
  return <section className="demo-pricing" aria-label="Illustrative valuation calculator">
    <div className="demo-notice"><strong>Fictional ExampleCo · interactive calculation</strong><span>Every input and history point here is made up to demonstrate the product. These are not Meta price targets.</span></div>
    <div className="demo-methods" role="group" aria-label="Valuation method">{METHODS.map(item => <button type="button" key={item} aria-pressed={method === item} onClick={() => { setMethod(item); setMultiple(methodInfo[item].multiple) }}>{item}</button>)}</div>
    <p>{methodInfo[method].use}</p>
    <div className="demo-controls"><label>Annual growth: {growth}%<input type="range" min="-20" max="30" value={growth} onChange={e => setGrowth(Number(e.target.value))} /></label><label>Valuation multiple<input type="number" min="0.1" max="60" step="0.1" value={multiple} onChange={e => setMultiple(Math.max(.1, Math.min(60, Number(e.target.value) || .1)))} /></label></div>
    <div className="demo-stats"><div><span>Implied value today</span><strong>{money(current)}</strong></div><div><span>12-month value</span><strong>{money(future)}</strong></div><div><span>Forecast interval</span><strong>1 year</strong></div></div>
    <details><summary>Show the calculation</summary><p>{methodInfo[method].formula}. Future baseline = {methodInfo[method].baseline} × (1 + {growth}%)¹. Today uses the starting baseline with no growth.</p><p>{methodInfo[method].label}: {methodInfo[method].baseline}. Fictional shares: 1.86 billion. EV bridge: $31.7 billion cash, $19.4 billion debt and $2.1 billion other claims. P/NAV uses assumed equity NAV; P/book history below is a separate accounting measure.</p></details>
    <ValuationResearchContext context={illustrativeHistory()} sourceLinks={() => null} prose={text => text} />
  </section>
}
function MemoryDemo() {
  const [selected, setSelected] = useState('meta')
  const reader = useRef<HTMLDivElement>(null)
  const note = demoNotes.find(item => item.id === selected)!
  return <><div className="demo-section-heading"><span className="eyebrow">Follow the evidence</span><h1>A shared company notebook</h1><p>Explore these public example notes. The same 3D explorer is used in the local app.</p></div><div className="memory-workspace"><MemoryExplorer graph={demoGraph} selectedId={selected} onSelect={setSelected} onRead={() => { reader.current?.scrollIntoView({ block: 'start' }); reader.current?.querySelector('aside')?.focus({ preventScroll: true }) }} /><div ref={reader}><MemoryDetail note={note} node={note} loading={false} error="" onSelect={setSelected} onRetry={() => {}} /></div></div></>
}
export default function DemoApp() {
  const [section, setSection] = useState<Section>('Research')
  const [query, setQuery] = useState('')
  const [notice, setNotice] = useState('')
  const [openCase, setOpenCase] = useState(false)
  const [step, setStep] = useState(0)
  const [challenge, setChallenge] = useState(false)
  const [retry, setRetry] = useState(false)
  const [allocation, setAllocation] = useState(8)
  const caseRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const sync = () => {
      let label: string
      try { label = decodeURIComponent(window.location.hash.slice(1)).toLowerCase() } catch { return }
      const next = sections.find(s => s.toLowerCase().replaceAll(' ', '-') === label)
      if (next) setSection(next)
    }
    sync(); window.addEventListener('hashchange', sync); return () => window.removeEventListener('hashchange', sync)
  }, [])
  function navigate(next: Section) { window.location.hash = next.toLowerCase().replaceAll(' ', '-'); setSection(next) }
  function explore() { setOpenCase(true); setStep(0); setNotice(''); navigate('Research'); requestAnimationFrame(() => caseRef.current?.scrollIntoView({ block: 'start' })) }
  return <div className="demo-app">
    <a className="skip-link" href="#demo-main">Skip to content</a>
    <header className="demo-topbar"><a className="demo-brand" href="#research">ResearchCouncil <span>Public demo</span></a><Source href={repositoryUrl} label="Run locally / source" /></header>
    <nav className="demo-nav" aria-label="Primary navigation">{sections.map(item => <a key={item} href={`#${item.toLowerCase().replaceAll(' ', '-')}`} aria-current={section === item ? 'page' : undefined}>{item}</a>)}</nav>
    <main id="demo-main" tabIndex={-1}>
      {section === 'Research' && <>
        <div className="demo-section-heading"><span className="eyebrow">From an idea to a reasoned decision</span><h1>What is worth a closer look?</h1><p>Explore one research journey. See the evidence, challenge the assumptions, then decide what to do next.</p></div>
        <form className="demo-search" onSubmit={e => { e.preventDefault(); if (!query.trim() || /^(?:\$?meta|meta platforms)$/i.test(query.trim())) explore(); else setNotice('This public demo includes META. Run the local app to research another ticker.') }}><label className="sr-only" htmlFor="demo-ticker">Demo ticker</label><input id="demo-ticker" value={query} onChange={e => setQuery(e.target.value)} placeholder="Try META to explore its capital spending…" maxLength={50} /><button className="demo-primary" type="submit">Explore example →</button></form>
        {notice && <p role="status" className="demo-notice">{notice}</p>}
        <MarketNews snapshotUrl={`${import.meta.env.BASE_URL}market-news.json`} />
        <section className="demo-research-start"><div><span className="eyebrow">Data checked September 26, 2026 · Q2 FY2026</span><h2>META: can growth and Muse earn back the spending?</h2><p>Reported earnings, a later product launch, and the evidence needed before acting.</p></div><button type="button" className="demo-primary" onClick={explore}>{openCase ? 'Back to earnings' : 'Open META research'} →</button></section>
        {openCase && <section className="demo-case" ref={caseRef} aria-label="META research walkthrough"><div className="demo-case-heading"><div><span className="eyebrow">META · Q2 reported July 29, 2026 · Period ended June 30</span><h2>Follow the investment process</h2></div><span className="demo-badge">As of September 26, 2026</span></div>
          <nav className="demo-steps" aria-label="Investment process">{steps.map((title, i) => <button type="button" key={title} aria-current={step === i ? 'step' : undefined} onClick={() => setStep(i)}><span>{i + 1}</span>{title}</button>)}</nav>
          {step === 0 && <EarningsSnapshot onNext={() => setStep(1)} />}
          {step === 1 && <div className="demo-step-body"><h3>Five questions, in plain language</h3><ol className="demo-questions">{questions.map(item => <li key={item.title}><h4>{item.title}</h4><p>{item.answer}</p>{item.source && <details><summary>Evidence and context</summary>{item.context && <p>{item.context}</p>}<Source href={item.source} label="Read the company material" /></details>}</li>)}</ol><p className="demo-muted">These are research questions written for this walkthrough, not a transcription of the earnings call.</p><button type="button" className="demo-primary" onClick={() => setStep(2)}>Try the pricing tools →</button></div>}
          {step === 2 && <div className="demo-step-body"><h3>Price depends on assumptions</h3><PricingExercise /><button type="button" className="demo-primary" onClick={() => setStep(3)}>See the decision framework →</button></div>}
          {step === 3 && <div className="demo-step-body"><span className="demo-badge">Illustrative outcome</span><h3>Watch, verify, then reassess</h3><p>The demo has no current price or personal portfolio constraints, so it cannot establish an investment recommendation.</p><ul><li>Check whether cash generation catches up with infrastructure spending.</li><li>Track Muse’s paid adoption and costs before assigning it a profit contribution.</li><li>Use a dated market price, supported valuation and downside limit before sizing.</li></ul><div className="demo-action-row"><button type="button" className="demo-primary" onClick={() => setChallenge(value => !value)}>{challenge ? 'Hide opposing views' : 'Challenge this idea'}</button><button type="button" className="button" onClick={() => setRetry(true)}>Retry this evidence gap</button></div>{challenge && <div className="demo-debate"><article><h4>Positive case</h4><p>{museThesis.positive}</p></article><article><h4>Opposing case</h4><p>{museThesis.opposing}</p></article><article><h4>What settles it?</h4><p>{museThesis.evidence} These are prewritten interpretations; no agent ran in your browser.</p></article></div>}{retry && <p role="status" className="demo-notice">In the local app, this button retries only the selected evidence gap and saves a new receipt. The public demo does not collect private research or start an agent.</p>}</div>}
        </section>}
        <details className="demo-how"><summary>How the local agent loop works</summary><ol><li>Resolve the ticker and latest reported earnings.</li><li>Collect the call, slides, release, filings and intervening updates.</li><li>Verify numerical facts and reuse source-bound company memory.</li><li>Answer five questions, calculate scenarios and record a decision.</li><li>On request, challenge the thesis or retry one missing piece.</li></ol><Source href={`${repositoryUrl}#readme`} label="Install the local app" /></details>
      </>}
      {section === 'Memory' && <MemoryDemo />}
      {section === 'Strategy testing' && <><div className="demo-section-heading"><span className="eyebrow">Try the assumptions</span><h1>What would change the price?</h1><p>A fictional company lets you explore each valuation method without presenting a made-up market forecast.</p></div><PricingExercise /></>}
      {section === 'Watchlist' && <><div className="demo-section-heading"><span className="eyebrow">A reason to come back</span><h1>Watch for a change, not just a ticker</h1></div><article className="demo-research-start"><div><span className="demo-badge">Example entry rule · fictional company</span><h2>ExampleCo</h2><p>Entry review: below $92.40 · Next check: earnings and cash conversion.</p><small>An illustrative review threshold, not a real order or recommendation.</small></div><button type="button" className="demo-primary" onClick={() => navigate('Strategy testing')}>Inspect pricing →</button></article><article className="demo-research-start"><div><span className="demo-badge">Public-source example</span><h2>META</h2><p>Q2 cash conversion and Muse adoption are the next checks. No live entry price is published in this demo.</p></div><button type="button" className="demo-primary" onClick={explore}>Review evidence →</button></article></>}
      {section === 'Portfolio' && <><div className="demo-section-heading"><span className="eyebrow">Understand position risk</span><h1>A good company can still be too large a position</h1><p>Fictional allocation exercise. No account is connected.</p></div><section className="demo-case demo-step-body"><label className="demo-allocation">Proposed position: {allocation}%<input type="range" min="1" max="25" value={allocation} onChange={e => setAllocation(Number(e.target.value))} /></label><div className="demo-allocation-bar"><span style={{width:`${allocation}%`}} /></div><p role="status">{allocation > 10 ? 'Above this example’s 10% position limit. Reduce size or review the limit before acting.' : 'Within this example’s 10% position limit. Downside and liquidity still need review.'}</p><button type="button" className="demo-primary" onClick={() => navigate('Strategy testing')}>Explore the downside assumptions →</button></section></>}
      {section === 'Congress' && <><div className="demo-section-heading"><span className="eyebrow">An idea source, not an endorsement</span><h1>Disclosures start questions</h1><p>Disclosure dates and transaction dates differ. A filing should lead to independent research.</p></div><article className="demo-research-start"><div><span className="demo-badge">Illustrative intake · not a real congressional trade</span><h2>New disclosure → company research</h2><p>The local app keeps filing links, date ranges and ownership context, then routes an idea into the same earnings-first process.</p><div className="demo-source-row"><Source href="https://disclosures-clerk.house.gov/" label="House disclosures" /><Source href="https://efdsearch.senate.gov/search/" label="Senate disclosures" /></div></div><button type="button" className="demo-primary" onClick={explore}>Explore the research journey →</button></article></>}
    </main><footer className="demo-footer"><span>Public demo · primary-source facts and labeled examples · agents run locally</span><Source href={repositoryUrl} label="Source & setup" /></footer>
  </div>
}
