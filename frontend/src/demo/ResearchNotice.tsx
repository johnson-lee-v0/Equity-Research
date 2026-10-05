export default function ResearchNotice({ scenarios = false }: { scenarios?: boolean }) {
  return <aside className="demo-risk-notice" aria-label={scenarios ? 'Scenario limitations' : 'Research and investment risk notice'}>
    <strong>{scenarios ? 'Hypothetical calculations, not expected returns' : 'Education and research only'}</strong>
    <p>{scenarios
      ? 'Reported figures and market observations are dated; future growth, multiples and scenario prices are assumptions. Changing assumptions changes the calculation, not a market outcome. These scenarios are not probabilities or a worst-case limit. Returns are not guaranteed, and real investments can lose their entire value.'
      : 'This historical, static demo is not personalized investment advice or a recommendation to buy, sell or hold a security. Returns are not guaranteed; investing can result in a total loss.'}</p>
    <p>Data, calculations and model outputs can contain errors or become stale. Independently verify current primary sources and assumptions before relying on any research; seek qualified advice when appropriate.</p>
  </aside>
}
