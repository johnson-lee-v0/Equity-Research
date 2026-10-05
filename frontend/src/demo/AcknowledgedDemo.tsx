import { Component, lazy, Suspense, useEffect, useRef, useState, type ReactNode } from 'react'
import ResearchNotice from './ResearchNotice'
import './acknowledgment.css'

// The real-company walkthrough is not mounted until the visitor acknowledges
// its limits. Acceptance lasts only for this page visit; no receipt is stored.
const MetaDemo = lazy(() => import('./DemoApp'))

export class ResearchLoadBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() {
    return this.state.failed ? <main className="research-entry" role="alert">
      <h1>The research example could not load.</h1>
      <p>A file may be unavailable or this tab may predate an update. Reload to open the current version. You will be asked to acknowledge the notice again.</p>
      <button className="research-notice-primary" type="button" onClick={() => window.location.reload()}>Reload the example</button>
    </main> : this.props.children
  }
}

export default function AcknowledgedDemo() {
  const [accepted, setAccepted] = useState(false)
  const [checked, setChecked] = useState(false)
  const dialog = useRef<HTMLDialogElement>(null)
  const opener = useRef<HTMLButtonElement>(null)
  const heading = useRef<HTMLHeadingElement>(null)

  function openNotice() {
    setChecked(false)
    if (!dialog.current?.open) dialog.current?.showModal()
    heading.current?.focus()
  }
  function closeNotice() {
    dialog.current?.close()
    opener.current?.focus()
  }

  useEffect(() => {
    openNotice()
    return () => { dialog.current?.close() }
  }, [])

  useEffect(() => {
    if (accepted) opener.current?.focus()
  }, [accepted])

  return <>
    {!accepted ? <main className="research-entry" id="research-overview">
      <a className="research-entry-brand" href="https://github.com/johnson-lee-v0/Equity-Research" target="_blank" rel="noreferrer">ResearchCouncil · Public demo ↗</a>
      <p className="research-entry-label">Real company. Dated evidence. Explicit assumptions.</p>
      <h1>Research, from question to reviewed decision.</h1>
      <p>Explore the engineering behind an end-to-end multi-agent research workflow: source discovery, earnings and fundamental analysis, code-backed valuation, CIO review and shared company memory. The sourced META walkthrough makes those stages inspectable. It is a static historical example—not a live agent run, a current price feed or a trading service.</p>
      <ResearchNotice />
      <button ref={opener} type="button" className="research-notice-primary" onClick={openNotice} aria-haspopup="dialog">Review notice &amp; explore the research workflow</button>
      <p className="research-notice-fine">Dismissed the notice? This overview remains available. The interactive example stays closed until you acknowledge it.</p>
    </main> : <>
      <div className="research-accepted-banner"><span>Historical META example · educational research, not investment advice</span><button ref={opener} type="button" onClick={openNotice} aria-haspopup="dialog">Review notice</button></div>
      <ResearchLoadBoundary><Suspense fallback={<p className="research-entry" role="status">Opening the META research example…</p>}><MetaDemo /></Suspense></ResearchLoadBoundary>
    </>}
    {!accepted && <footer className="research-license-footer">
      <span>Original code: MIT. Source material retains its own terms. Hosting providers may log requests.</span>
      <a href="./LICENSE.txt">Code license</a><a href="./THIRD_PARTY_NOTICES.txt">Third-party notices</a>
    </footer>}
    <dialog ref={dialog} className="research-ack-dialog" aria-labelledby="research-ack-title" aria-describedby="research-ack-description" onCancel={() => { setChecked(false) }} onClose={() => { opener.current?.focus() }}>
      <div className="research-ack-topbar"><button className="research-ack-top-close" type="button" onClick={closeNotice} aria-label={accepted ? 'Close notice and return to research' : 'Close notice and return to overview'}>Close notice <span aria-hidden="true">×</span></button></div>
      <form onSubmit={event => {
        event.preventDefault()
        if (!checked) return
        setAccepted(true)
        dialog.current?.close()
      }}>
        <p className="research-entry-label">Before you interact</p>
        <h2 ref={heading} tabIndex={-1} id="research-ack-title">Understand the limits.</h2>
        <div id="research-ack-description">
          <p>This example is for education and research only. It is not personalized financial advice or a recommendation to buy, sell or hold any security.</p>
          <p>Company figures and market observations are dated. Valuation scenarios are hypothetical, not predictions or guaranteed returns. Sources, calculations and model outputs can contain errors or become stale. Investments can lose their entire value.</p>
          <p>No warranty of accuracy, completeness or fitness for an investment decision is made. Independently verify current primary sources and seek qualified advice when appropriate. This demo cannot place trades or access your accounts.</p>
          <p>Acknowledging this notice does not create a legal release or waive your legal rights.</p>
        </div>
        <label className="research-ack-check"><input type="checkbox" required checked={checked} onChange={event => setChecked(event.target.checked)} /><span>I understand the risks and that this example is educational research, not investment advice.</span></label>
        <div className="research-ack-actions"><button className="research-notice-primary" type="submit" disabled={!checked}>{accepted ? 'I understand — return to research' : 'I understand — open META'}</button><button type="button" onClick={closeNotice}>{accepted ? 'Close notice' : 'Back to overview'}</button></div>
        <p className="research-notice-fine">Only for this page visit. No acknowledgment is saved or sent. Reloading requires a new acknowledgment.</p>
      </form>
    </dialog>
  </>
}
