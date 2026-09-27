import { useEffect, useRef, useState } from 'react'
import MarketNews from '../components/MarketNews'
import EarningsTrendExplorer from '../panels/research/EarningsTrendExplorer'
import { MemoryDetail, MemoryExplorer } from '../panels/Memory'
import { demoGraph, demoNotes, META_RELEASE, META_SLIDES, MUSE_RELEASE, latestFinancials, museThesis, questions } from './demoData'
import { metaTrends } from './metaTrends'
import MetaPricing, { MetaDecision, MetaWatchlist } from './MetaPricing'
import EarningsCallReview from './EarningsCallReview'
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
    <EarningsTrendExplorer result={metaTrends} />
    <details><summary>Quarterly calculation and spending outlook</summary><p>Q2 CapEx = ${latestFinancials.cashPpe}bn cash PP&E + ${latestFinancials.financeLeasePrincipal}bn lease principal. The first-half total, ${latestFinancials.yearToDateCapex}bn, is not a quarterly value.</p><p>FY2026 CapEx guidance: $130–145bn, as given July 29. This is management’s forecast, not spending already reported.</p><p>Diluted EPS: ${latestFinancials.dilutedEps}, versus ${latestFinancials.priorDilutedEps}. Operating cash flow: ${latestFinancials.operatingCashFlow}bn.</p><Source href={`${META_SLIDES}#page=15`} label="Open cash-flow reconciliation" /><span> · </span><Source href={META_RELEASE} label="EPS and annual guidance source" /></details>
    <div className="demo-source-row"><Source href={META_RELEASE} label="Q2 earnings release" /><Source href={META_SLIDES} label="Q2 earnings slides" /></div>
    <EarningsCallReview />
    <MuseCatalyst />
    <button type="button" className="demo-primary" onClick={onNext}>Review the five questions →</button>
  </div>
}
function MemoryDemo() {
  const [selected, setSelected] = useState('meta')
  const reader = useRef<HTMLDivElement>(null)
  const note = demoNotes.find(item => item.id === selected)!
  return <><div className="demo-section-heading"><span className="eyebrow">Follow the evidence</span><h1>META’s shared research notebook</h1><p>A public evidence notebook authored from the linked sources. Follow reported results, the earnings call, Muse and valuation assumptions.</p></div><div className="memory-workspace"><MemoryExplorer graph={demoGraph} selectedId={selected} onSelect={setSelected} onRead={() => { reader.current?.scrollIntoView({ block: 'start' }); reader.current?.querySelector('aside')?.focus({ preventScroll: true }) }} /><div ref={reader}><MemoryDetail note={note} node={note} loading={false} error="" onSelect={setSelected} onRetry={() => {}} /></div></div></>
}
export default function DemoApp() {
  const [section, setSection] = useState<Section>('Research')
  const [query, setQuery] = useState('')
  const [notice, setNotice] = useState('')
  const [openCase, setOpenCase] = useState(false)
  const [step, setStep] = useState(0)
  const [challenge, setChallenge] = useState(false)
  const [retry, setRetry] = useState(false)
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
        <div className="demo-section-heading"><span className="eyebrow">From an idea to a reasoned decision</span><h1>What is worth a closer look?</h1><p>Review META’s reported results, what management said, and a valuation built from dated financial evidence.</p></div>
        <form className="demo-search" onSubmit={e => { e.preventDefault(); if (!query.trim() || /^(?:\$?meta|meta platforms)$/i.test(query.trim())) explore(); else setNotice('This public demo includes META. Run the local app to research another ticker.') }}><label className="sr-only" htmlFor="demo-ticker">Demo ticker</label><input id="demo-ticker" value={query} onChange={e => setQuery(e.target.value)} placeholder="Try META to explore its capital spending…" maxLength={50} /><button className="demo-primary" type="submit">Research META →</button></form>
        {notice && <p role="status" className="demo-notice">{notice}</p>}
        <MarketNews snapshotUrl={`${import.meta.env.BASE_URL}market-news.json`} />
        <section className="demo-research-start"><div><span className="eyebrow">Data checked September 26, 2026 · Q2 FY2026</span><h2>META: can growth and Muse earn back the spending?</h2><p>Reported earnings, a later product launch, and the evidence needed before acting.</p></div><button type="button" className="demo-primary" onClick={explore}>{openCase ? 'Back to earnings' : 'Open META research'} →</button></section>
        {openCase && <section className="demo-case" ref={caseRef} aria-label="META research walkthrough"><div className="demo-case-heading"><div><span className="eyebrow">META · Q2 reported July 29, 2026 · Period ended June 30</span><h2>Follow the investment process</h2></div><span className="demo-badge">As of September 26, 2026</span></div>
          <nav className="demo-steps" aria-label="Investment process">{steps.map((title, i) => <button type="button" key={title} aria-current={step === i ? 'step' : undefined} onClick={() => setStep(i)}><span>{i + 1}</span>{title}</button>)}</nav>
          {step === 0 && <EarningsSnapshot onNext={() => setStep(1)} />}
          {step === 1 && <div className="demo-step-body"><h3>Five questions, in plain language</h3><ol className="demo-questions">{questions.map(item => <li key={item.title}><h4>{item.title}</h4><p>{item.answer}</p>{item.source && <details><summary>Evidence and context</summary>{item.context && <p>{item.context}</p>}<Source href={item.source} label="Read the company material" /></details>}</li>)}</ol><p className="demo-muted">These are research questions written for this walkthrough, not a transcription of the earnings call.</p><button type="button" className="demo-primary" onClick={() => setStep(2)}>Review META pricing →</button></div>}
          {step === 2 && <div className="demo-step-body"><h3>Price depends on assumptions</h3><MetaPricing /><button type="button" className="demo-primary" onClick={() => setStep(3)}>See the decision framework →</button></div>}
          {step === 3 && <div className="demo-step-body"><MetaDecision /><div className="demo-action-row"><button type="button" className="demo-primary" onClick={() => setChallenge(value => !value)}>{challenge ? 'Hide opposing views' : 'Challenge this idea'}</button><button type="button" className="button" onClick={() => setRetry(true)}>Inspect this evidence gap</button></div>{challenge && <div className="demo-debate"><article><h4>Positive case</h4><p>{museThesis.positive}</p></article><article><h4>Opposing case</h4><p>{museThesis.opposing}</p></article><article><h4>What settles it?</h4><p>{museThesis.evidence} This published analysis is based on the linked sources; new research runs in the local app.</p></article></div>}{retry && <div role="status" className="demo-notice"><strong>Muse standalone economics remain undisclosed</strong><p>The launch provides no separate revenue, paid-conversion or serving-cost figures. Check the launch source and the next earnings update. A targeted evidence refresh requires the local app.</p><Source href={MUSE_RELEASE} label="Review the launch evidence" /><span><Source href={`${repositoryUrl}#readme`} label="Run an evidence refresh locally" /></span></div>}</div>}
        </section>}
        <details className="demo-how"><summary>How the local agent loop works</summary><ol><li>Resolve the ticker and latest reported earnings.</li><li>Collect the call, slides, release, filings and intervening updates.</li><li>Verify numerical facts and reuse source-bound company memory.</li><li>Answer five questions, calculate scenarios and record a decision.</li><li>On request, challenge the thesis or retry one missing piece.</li></ol><Source href={`${repositoryUrl}#readme`} label="Install the local app" /></details>
      </>}
      {section === 'Memory' && <MemoryDemo />}
      {section === 'Strategy testing' && <><div className="demo-section-heading"><span className="eyebrow">Sensitivity analysis · META</span><h1>What would change META’s value?</h1><p>Change the growth and multiple assumptions against the same reported financial baseline used in the research case.</p></div><MetaPricing /></>}
      {section === 'Watchlist' && <><div className="demo-section-heading"><span className="eyebrow">META · Price and evidence triggers</span><h1>What would bring META into range?</h1></div><MetaWatchlist onPricing={() => navigate('Strategy testing')} onResearch={explore} /></>}
      {section === 'Portfolio' && <><div className="demo-section-heading"><span className="eyebrow">Portfolio</span><h1>No holdings are published</h1><p>This research case does not contain an account balance, position or personal risk limit. Portfolio sizing requires those inputs in the local app.</p></div><section className="demo-case demo-step-body"><h2>Review the investment before sizing it</h2><p>The public META case provides a dated market price, scenario values and investment risks. A position size is not inferred from company results.</p><button type="button" className="demo-primary" onClick={() => navigate('Strategy testing')}>Review META’s downside →</button><div className="demo-source-row"><Source href={`${repositoryUrl}#readme`} label="Connect your portfolio locally" /></div></section></>}
      {section === 'Congress' && <><div className="demo-section-heading"><span className="eyebrow">Congress disclosures</span><h1>No reviewed filings are published</h1><p>The META case starts from company earnings. It is not attributed to a congressional transaction.</p></div><section className="demo-case demo-step-body"><h2>Start with an original filing</h2><p>Use the official registers to inspect the filer, transaction date, disclosure date and amount range. The local app can turn a verified filing into a research case.</p><div className="demo-source-row"><Source href="https://disclosures-clerk.house.gov/" label="House disclosures" /><Source href="https://efdsearch.senate.gov/search/" label="Senate disclosures" /><Source href={`${repositoryUrl}#readme`} label="Set up local collection" /></div></section></>}
    </main><footer className="demo-footer"><span>META research · dated sources and explicit forecasts · agents run locally</span><Source href={repositoryUrl} label="Source & setup" /></footer>
  </div>
}
