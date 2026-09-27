import type { ReactNode } from 'react'
import { contextNumberLabel } from './priceTargetModel'

export default function ComparableAnalysis({ value, sourceLinks }: { value: unknown; sourceLinks: (refs: unknown) => ReactNode }) {
  if (!value || typeof value !== 'object') return null
  const comparison = value as { metric?: string; coverage_note?: string; peers?: Array<{ ticker: string; basis: string; as_of: string; multiple: string; rationale: string; ebitda_basis?: string; share_basis_coverage?: string; source_refs: string[] }>; medians?: Record<string, string>; rejected?: Array<{ ticker: string; reason: string }> }
  return <section className="price-target-context-section"><div className="price-target-context-heading"><h3>Comparable companies</h3><span>{comparison.metric}</span></div>
    <p className="price-target-note">{comparison.coverage_note}</p>
    {!!comparison.peers?.length && <><dl className="price-target-context-stats">{Object.entries(comparison.medians ?? {}).map(([basis, value]) => <div key={basis}><dt>{basis} peer median</dt><dd>{contextNumberLabel(value)}×</dd></div>)}</dl><div className="price-target-table-wrap"><table><thead><tr><th>Company</th><th>Observed multiple</th><th>Basis and date</th><th>Why comparable</th></tr></thead><tbody>{comparison.peers.map((peer, index) => <tr key={`${peer.ticker}-${index}`}><th>{peer.ticker}</th><td>{contextNumberLabel(peer.multiple)}×{sourceLinks(peer.source_refs)}</td><td>{peer.basis}<small>{peer.as_of}</small>{peer.ebitda_basis && <small>{peer.ebitda_basis}</small>}</td><td>{peer.rationale}{peer.share_basis_coverage && peer.share_basis_coverage !== 'complete' && <small>{peer.share_basis_coverage}</small>}</td></tr>)}</tbody></table></div></>}
    {!!comparison.rejected?.length && <details><summary>Unverified comparisons</summary><ul>{comparison.rejected.map((peer, index) => <li key={index}>{peer.ticker}: {peer.reason}</li>)}</ul></details>}
  </section>
}
