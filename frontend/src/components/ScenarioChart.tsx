import { useState } from 'react'

type Row = Record<string, unknown>
function number(value: unknown) { return value == null || value === '' ? NaN : Number(value) }
function point(row: Row, index: number) {
  return { day: number(row.trading_day ?? index), low: number(row.p05 ?? row.low ?? row.lower ?? row.q10 ?? row.q25), high: number(row.p95 ?? row.high ?? row.upper ?? row.q90 ?? row.q75), median: number(row.p50 ?? row.median ?? row.q50) }
}
const price = (value: number) => Number.isFinite(value) ? value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : 'Unknown'

export function ScenarioChart({ rows, quantiles }: { rows: Row[]; quantiles: Row[] }) {
  const [selected, setSelected] = useState('')
  const groups = new Map<string, Row[]>()
  for (const row of rows) {
    const name = String(row.label ?? row.scenario ?? 'Scenario').replace(/ · day\s*\d*$/, '')
    groups.set(name, [...(groups.get(name) ?? []), row])
  }
  const active = groups.has(selected) ? selected : [...groups.keys()][0] ?? ''
  const series = groups.get(active) ?? []
  const points = series.map(point).filter(p => Object.values(p).every(Number.isFinite)).sort((a,b) => a.day-b.day)
  const currency = String(series[0]?.currency ?? '')
  const min = points.length ? Math.min(...points.map(p=>p.low)) : 0
  const max = points.length ? Math.max(...points.map(p=>p.high)) : 1
  const span = max-min || Math.max(max*.02, 1)
  const dayMax = Math.max(1, ...points.map(p=>p.day))
  const x = (day: number) => 65 + day/dayMax*650
  const y = (value: number) => 230-(value-min)/span*190
  const band = [...points.map(p=>`${x(p.day)},${y(p.high)}`), ...[...points].reverse().map(p=>`${x(p.day)},${y(p.low)}`)].join(' ')
  const terminals: Row[] = quantiles.length ? quantiles : [...groups.entries()].map(([label, values]) => ({...values[values.length-1],label}))
  return <div className="price-scenario-chart">
    {groups.size > 1 && <label>Candidate and scenario <select aria-label="Candidate and scenario" value={active} onChange={event=>setSelected(event.target.value)}>{[...groups.keys()].map(name=><option key={name}>{name}</option>)}</select></label>}
    {points.length > 1 ? <><svg viewBox="0 0 750 280" role="img" aria-label={`${active}: conditional 5th to 95th percentile price band over ${dayMax} trading days`}>
      {[0,.5,1].map(t=><g key={t}><line x1="65" x2="715" y1={y(min+span*t)} y2={y(min+span*t)} stroke="currentColor" opacity=".12"/><text x="58" y={y(min+span*t)+4} textAnchor="end">{price(min+span*t)}</text></g>)}
      <polygon points={band} fill="#9f82da" opacity=".28"/>
      <polyline points={points.map(p=>`${x(p.day)},${y(p.median)}`).join(' ')} fill="none" stroke="#9f82da" strokeWidth="2.5"/>
      <text x="65" y="258">Day 0</text><text x="715" y="258" textAnchor="end">Day {dayMax}</text><text x="65" y="20">{currency} per share · {active}</text>
    </svg><p className="muted-copy">Shaded band: 5th–95th percentiles. Line: median. Horizontal axis: trading days. Bear and bull shift returns below or above the historical base; all three cases can still end lower.</p></> : <p className="muted-copy">No complete price path was returned for this scenario.</p>}
    {terminals.length > 0 && <div className="scenario-comparison"><table><caption>Terminal price sensitivity · conditional scenarios</caption><thead><tr><th>Scenario</th><th>5th percentile</th><th>Median</th><th>95th percentile</th></tr></thead><tbody>{terminals.map((row,index)=>{const p=point(row,index); return <tr key={String(row.label ?? index)}><th>{String(row.label ?? `Scenario ${index+1}`)} {String(row.currency ?? '')}</th><td>{price(p.low)}</td><td>{price(p.median)}</td><td>{price(p.high)}</td></tr>})}</tbody></table></div>}
  </div>
}
