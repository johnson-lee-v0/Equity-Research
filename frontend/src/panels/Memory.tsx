import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { apiFetch } from '../api'
import { Icon } from '../components/Icon'
import type { Namespace } from '../types'
import { linkedMemoryNodes, memoryBody, memoryGraphQuery, memoryKind, safeMemoryUrl, type SharedMemoryGraph, type SharedMemoryNote, type SharedMemoryNode } from './memoryGraphModel'
import type { MemoryCameraAction } from './MemoryGraph3D'
import './memory.css'

const MemoryGraph3D = lazy(() => import('./MemoryGraph3D'))

function dateLabel(value: string | null) {
  if (!value) return 'Date unavailable'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

function NoteBody({ note, onSelect }: { note: SharedMemoryNote; onSelect: (id: string) => void }) {
  function inline(text: string) {
    const parts: ReactNode[] = []
    const pattern = /\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g
    let previous = 0
    for (const match of text.matchAll(pattern)) {
      parts.push(text.slice(previous, match.index))
      const label = match[2] || match[1].split('/').at(-1) || match[1]
      const link = note.links.find((item) => item.label === label || item.target === match[1])
      parts.push(link ? <button type="button" className="memory-inline-link" key={`${match.index}:${link.target}`} onClick={() => onSelect(link.target)}>{label}</button> : <span key={match.index}>{label}</span>)
      previous = match.index! + match[0].length
    }
    parts.push(text.slice(previous))
    return parts
  }
  const blocks: ReactNode[] = []
  const lines = memoryBody(note.markdown).split('\n')
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index].trim()
    if (!line) continue
    if (/^#{1,6}\s/.test(line)) {
      const text = line.replace(/^#{1,6}\s+/, '')
      if (text === note.title && blocks.length === 0) continue
      blocks.push(<h3 key={index}>{inline(text)}</h3>)
    } else if (/^[-*]\s/.test(line)) {
      const items = [line.replace(/^[-*]\s+/, '')]
      const first = index
      while (index + 1 < lines.length && /^[-*]\s/.test(lines[index + 1].trim())) items.push(lines[++index].trim().replace(/^[-*]\s+/, ''))
      blocks.push(<ul key={first}>{items.map((item, itemIndex) => <li key={itemIndex}>{inline(item)}</li>)}</ul>)
    } else {
      blocks.push(<p key={index}>{inline(line)}</p>)
    }
  }
  return <div className="memory-note-body">{blocks.length ? blocks : <p>This note has no body text yet.</p>}</div>
}

export function MemoryNoteList({ nodes, selectedId, onSelect }: { nodes: SharedMemoryNode[]; selectedId: string | null; onSelect: (id: string) => void }) {
  return <div className="memory-note-list" aria-label="Memory notes">{nodes.map((node) => <button type="button" key={node.id} className={node.id === selectedId ? 'is-selected' : ''} onClick={() => onSelect(node.id)} aria-pressed={node.id === selectedId}>
    <i style={{ background: memoryKind(node.kind).color }} /><span>{node.title}<small>{memoryKind(node.kind).label}{node.ticker ? ` · ${node.ticker}` : ''}</small></span><Icon name="chevron" size={15} />
  </button>)}</div>
}

export function MemoryExplorer({ graph, selectedId, onSelect, onRead }: { graph: SharedMemoryGraph; selectedId: string | null; onSelect: (id: string) => void; onRead: () => void }) {
  const [mode, setMode] = useState<'3d' | 'list'>('3d')
  const [unavailable, setUnavailable] = useState(false)
  const [connectionsOnly, setConnectionsOnly] = useState(false)
  const [cameraAction, setCameraAction] = useState<MemoryCameraAction>({ type: 'fit', nonce: 0 })
  const selected = graph.nodes.find((node) => node.id === selectedId)
  const connected = useMemo(() => linkedMemoryNodes(selectedId, graph.edges), [selectedId, graph.edges])
  const isolate = connectionsOnly && Boolean(selected)
  const nodes = useMemo(() => isolate ? graph.nodes.filter((node) => connected.has(node.id)) : graph.nodes, [isolate, graph.nodes, connected])
  const links = isolate ? graph.edges.filter((edge) => connected.has(edge.source) && connected.has(edge.target)).length : graph.edges.length
  const companies = graph.nodes.filter((node) => node.kind === 'company')
  const showList = mode === 'list' || unavailable
  const camera = (type: MemoryCameraAction['type']) => setCameraAction((current) => ({ type, nonce: current.nonce + 1 }))
  const onUnavailable = useCallback(() => { setUnavailable(true); setMode('list') }, [])
  return <section className="memory-graph-card" aria-label="Shared memory explorer">
    <div className="memory-graph-toolbar">
      <div><strong>Connected research</strong><span aria-live="polite">{nodes.length.toLocaleString()} notes · {links.toLocaleString()} links{isolate ? ' · selected connections' : ''}</span></div>
      <div className="memory-view-switch" role="group" aria-label="Memory view">
        <button type="button" aria-pressed={!showList} disabled={unavailable} onClick={() => setMode('3d')}>3D map</button>
        <button type="button" aria-pressed={showList} onClick={() => setMode('list')}>List</button>
      </div>
    </div>
    <div className="memory-explorer-controls">
      {!showList && <div className="memory-zoom-controls" role="group" aria-label="Map controls">
        <button type="button" className="button" aria-label="Zoom out" onClick={() => camera('zoom_out')}>−</button>
        <button type="button" className="button" aria-label="Zoom in" onClick={() => camera('zoom_in')}>+</button>
        <button type="button" className="button" onClick={() => camera('fit')}>Reset view</button>
        <button type="button" className="button" disabled={!selected} onClick={() => camera('focus')}>Focus selected</button>
      </div>}
      <button type="button" className="button memory-connections-toggle" aria-pressed={isolate} disabled={!selected} onClick={() => setConnectionsOnly((current) => !current)}>Connections only</button>
    </div>
    {unavailable && <p className="memory-fallback" role="status">3D is unavailable on this device. You can still explore every note and connection in the list.</p>}
    {!showList && companies.length > 1 && <div className="memory-company-shortcuts" aria-label="Start with a company"><span>Start with</span>{companies.slice(0, 8).map((company) => <button type="button" key={company.id} aria-pressed={company.id === selectedId} onClick={() => { onSelect(company.id); camera('focus') }}>{company.ticker || company.title}</button>)}</div>}
    {showList ? <div className="memory-list-view"><MemoryNoteList nodes={nodes} selectedId={selectedId} onSelect={onSelect} /></div> : <div className="memory-graph-stage"><Suspense fallback={<p role="status" className="memory-loading">Opening 3D memory…</p>}><MemoryGraph3D graph={graph} selectedId={selectedId} onSelect={onSelect} cameraAction={cameraAction} visibleIds={isolate ? connected : undefined} onUnavailable={onUnavailable} /></Suspense></div>}
    <div className="memory-selection-bar" aria-live="polite"><div><span>{selected ? memoryKind(selected.kind).label : 'Explore your research'}</span><strong>{selected?.title || (selectedId ? 'Reading a note outside this view' : 'Select a company or note to begin')}</strong></div>{selectedId && <button type="button" className="button" onClick={onRead}>Read selected note <Icon name="chevron" size={14} /></button>}</div>
    <div className="memory-graph-foot">{showList ? <span>Select any note to read it and follow its connections.</span> : <><span>Drag to rotate · Scroll to zoom · Right-drag to move</span><span>Touch: drag to rotate · Pinch to zoom</span></>}{isolate && <span>Connections shown within the current filters. Read the note for all its links.</span>}</div>
    {!showList && <details className="memory-note-index"><summary>Browse {nodes.length.toLocaleString()} notes as a list</summary><MemoryNoteList nodes={nodes} selectedId={selectedId} onSelect={onSelect} /></details>}
  </section>
}

export function MemoryDetail({ note, node, loading, error, onSelect, onRetry, onBack, onReveal }: { note: SharedMemoryNote | null; node: SharedMemoryNode | null; loading: boolean; error: string; onSelect: (id: string) => void; onRetry: () => void; onBack?: () => void; onReveal?: () => void }) {
  const current = note || node
  const sourceUrl = safeMemoryUrl(note?.frontmatter.source_url ?? current?.source_url)
  return <aside className="memory-detail" aria-label="Selected memory note" aria-busy={loading} tabIndex={-1}>
    {onBack && <button type="button" className="button memory-note-back" onClick={onBack}>← Previous note</button>}
    {!current && !loading && !error ? <div className="memory-detail-empty"><Icon name="link" size={30} /><h2>Follow a connection</h2><p>Select a company, fact or document in the map or list to see what the agents can retrieve.</p></div> : <>
      {current && <><span className="memory-kind-badge" style={{ color: memoryKind(current.kind).color }}>{memoryKind(current.kind).label}{current.ticker ? ` · ${current.ticker}` : ''}</span><h2>{current.title}</h2><div className="memory-note-status">{current.status.replaceAll('_', ' ')} · {dateLabel(current.updated_at)}</div></>}
      {loading && <p role="status" className="muted-copy">Loading note…</p>}
      {error && <div className="memory-error" role="alert"><p>{error}</p><button type="button" className="button" onClick={onRetry}>Try again</button></div>}
      {note && <>
        {onReveal && <div className="memory-outside-view"><p>This note is outside the current map or filters.</p><button type="button" className="button" onClick={onReveal}>Find this note in the map</button></div>}
        {(['opinion', 'user_note', 'earnings'].includes(note.kind)) && <p className="memory-opinion-notice">This is an interpretation or personal note. Agents must verify supporting facts before using it as evidence.</p>}
        <NoteBody note={note} onSelect={onSelect} />
        {sourceUrl && <a className="button memory-source-link" href={sourceUrl} target="_blank" rel="noopener noreferrer">Read original source <Icon name="external" size={14} /></a>}
        {note.links.length > 0 && <section className="memory-linked-notes"><h3>Connected notes</h3>{note.links.map((link, index) => <button type="button" key={`${link.target}:${index}`} onClick={() => onSelect(link.target)}><Icon name="link" size={14} /><span>{link.label}</span><Icon name="chevron" size={14} /></button>)}</section>}
        <details className="memory-provenance"><summary>Source and history</summary><dl>{Object.entries(note.frontmatter).map(([key, value]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{typeof value === 'string' ? value : JSON.stringify(value)}</dd></div>)}</dl><p>Vault file: <code>{note.path}</code></p></details>
        <details className="memory-markdown"><summary>View Markdown</summary><pre>{note.markdown}</pre></details>
      </>}
    </>}
  </aside>
}

export default function Memory({ namespace }: { namespace: Namespace }) {
  const [graph, setGraph] = useState<SharedMemoryGraph | null>(null)
  const [filters, setFilters] = useState({ ticker: '', kind: '', query: '' })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [syncing, setSyncing] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [note, setNote] = useState<SharedMemoryNote | null>(null)
  const [noteLoading, setNoteLoading] = useState(false)
  const [noteError, setNoteError] = useState('')
  const [noteRefresh, setNoteRefresh] = useState(0)
  const [copied, setCopied] = useState(false)
  const [copyError, setCopyError] = useState('')
  const [history, setHistory] = useState<string[]>([])
  const selectedRef = useRef<string | null>(null)
  const selectionVersion = useRef(0)
  const detailRef = useRef<HTMLDivElement>(null)
  const selectCurrent = useCallback((id: string | null) => {
    if (id === selectedRef.current) return
    selectedRef.current = id
    selectionVersion.current += 1
    setSelectedId(id); setNote(null); setNoteError(''); setNoteLoading(Boolean(id))
  }, [])
  function selectNode(id: string) {
    if (id === selectedRef.current) return
    const previous = selectedRef.current
    if (previous) setHistory((items) => [...items.slice(-19), previous])
    selectCurrent(id)
  }
  function readSelected() {
    detailRef.current?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'start' })
    detailRef.current?.querySelector('aside')?.focus({ preventScroll: true })
  }
  async function refreshMemory() {
    setSyncing(true); setError('')
    try {
      await apiFetch(`/api/memory/sync?namespace=${encodeURIComponent(namespace)}`, { method: 'POST', body: '{}' }, { mutation: true })
      setRefresh((value) => value + 1)
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Memory could not be refreshed.') }
    finally { setSyncing(false) }
  }
  const query = memoryGraphQuery(namespace, filters)
  useEffect(() => {
    const controller = new AbortController()
    const selectionAtStart = selectionVersion.current
    setLoading(true); setError('')
    const timer = window.setTimeout(() => {
      apiFetch<SharedMemoryGraph>(query, {}, { signal: controller.signal }).then((next) => {
        if (controller.signal.aborted) return
        setGraph(next)
        const current = selectedRef.current
        // A newer reader choice wins over an in-flight filter response.
        if (selectionVersion.current === selectionAtStart) selectCurrent(current && next.nodes.some((node) => node.id === current) ? current : next.nodes.find((node) => node.kind === 'company')?.id ?? next.nodes[0]?.id ?? null)
      }).catch((failure) => {
        if (!controller.signal.aborted) { setError(failure instanceof Error ? failure.message : 'Shared memory is unavailable.'); setGraph(null); if (selectionVersion.current === selectionAtStart) selectCurrent(null) }
      }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    }, filters.query ? 220 : 0)
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [query, refresh, filters.query, selectCurrent])
  useEffect(() => {
    setNote(null); setNoteError(''); setNoteLoading(false)
    if (!selectedId) return
    const controller = new AbortController()
    setNoteLoading(true)
    apiFetch<SharedMemoryNote>(`/api/memory/notes/${encodeURIComponent(selectedId)}?namespace=${encodeURIComponent(namespace)}`, {}, { signal: controller.signal })
      .then((next) => { if (!controller.signal.aborted && selectedRef.current === selectedId) setNote(next) })
      .catch((failure) => { if (!controller.signal.aborted && selectedRef.current === selectedId) setNoteError(failure instanceof Error ? failure.message : 'This note could not be loaded.') })
      .finally(() => { if (!controller.signal.aborted && selectedRef.current === selectedId) setNoteLoading(false) })
    return () => controller.abort()
  }, [namespace, selectedId, noteRefresh, refresh])
  const selectedNode = graph?.nodes.find((node) => node.id === selectedId) ?? null
  const hasFilters = Boolean(filters.ticker || filters.kind || filters.query)
  const detail = <div className="memory-detail-column" ref={detailRef}><MemoryDetail note={note?.id === selectedId ? note : null} node={selectedNode} loading={noteLoading} error={noteError} onSelect={selectNode} onRetry={() => setNoteRefresh((value) => value + 1)} onBack={history.length ? () => { const previous = history.at(-1)!; setHistory((items) => items.slice(0, -1)); selectCurrent(previous) } : undefined} onReveal={note && note.id === selectedId && !selectedNode ? () => setFilters({ ticker: note.ticker || '', kind: '', query: note.title.slice(0, 300) }) : undefined} /></div>
  return <div className="memory-page">
    <header className="workspace-header memory-header"><div><span className="eyebrow">Shared research notebook</span><h1>Memory</h1><p>Explore what the agents know, where it came from and what still needs an answer.</p></div><button type="button" className="button" disabled={loading || syncing} onClick={() => void refreshMemory()}><Icon name="refresh" size={15} /> {syncing ? 'Refreshing…' : 'Refresh memory'}</button></header>
    <div className="memory-intro"><Icon name="book" size={21} /><div><strong>Your research, connected in 3D</strong><p>Start with a company, rotate the map and select a note. Follow its links to understand the evidence, analysis and unanswered questions behind it.</p></div></div>
    <form className="memory-filters" aria-label="Filter shared memory" onSubmit={(event) => event.preventDefault()}>
      <label className="memory-search">Search notes<div><Icon name="search" size={17} /><input type="search" value={filters.query} maxLength={300} placeholder="A company, topic or number…" onChange={(event) => setFilters((current) => ({ ...current, query: event.target.value }))} /></div></label>
      <label>Company<select value={filters.ticker} onChange={(event) => setFilters((current) => ({ ...current, ticker: event.target.value }))}><option value="">All companies</option>{[...new Set([...(graph?.tickers ?? []), ...(filters.ticker ? [filters.ticker] : [])])].sort().map((ticker) => <option key={ticker} value={ticker}>{ticker}</option>)}</select></label>
      <label>Note type<select value={filters.kind} onChange={(event) => setFilters((current) => ({ ...current, kind: event.target.value }))}><option value="">All note types</option>{[...new Set([...(graph?.kinds ?? []), ...(filters.kind ? [filters.kind] : [])])].sort().map((kind) => <option key={kind} value={kind}>{memoryKind(kind).label}</option>)}</select></label>
      {hasFilters && <button type="button" className="button" onClick={() => setFilters({ ticker: '', kind: '', query: '' })}>Clear filters</button>}
    </form>
    {loading && <p role="status" className="memory-loading">Loading connected memory…</p>}
    {error && <div role="alert" className="memory-error"><strong>Memory could not be loaded</strong><p>{error}</p><button type="button" className="button" onClick={() => setRefresh((value) => value + 1)}>Try again</button></div>}
    {graph && <>
      {graph.truncated && <p className="memory-limit" role="status">Showing {graph.nodes.length.toLocaleString()} of {graph.total_nodes.toLocaleString()} matching notes. Choose a company, note type or search term to explore a smaller graph.</p>}
      {graph.nodes.length ? <>
        <div className="memory-legend" aria-label="Note types">{[...new Set(graph.nodes.map((node) => node.kind))].map((kind) => <span key={kind} title={memoryKind(kind).description}><i style={{ background: memoryKind(kind).color }} />{memoryKind(kind).label}</span>)}</div>
        <div className={`memory-workspace${loading ? ' is-updating' : ''}`} aria-busy={loading}><div className="memory-visual-column"><MemoryExplorer graph={graph} selectedId={selectedId} onSelect={selectNode} onRead={readSelected} /></div>{detail}</div>
      </> : <div className="memory-empty"><Icon name="book" size={30} /><h2>{hasFilters ? 'No notes match these filters' : 'Your research will connect here'}</h2><p>{hasFilters ? 'Try a different company or search term, or clear the filters.' : 'As research is saved, company notes will link to documents, facts, analysis and open questions.'}</p>{hasFilters && <button type="button" className="button" onClick={() => setFilters({ ticker: '', kind: '', query: '' })}>Clear filters</button>}</div>}
      <details className="memory-vault"><summary>Open this notebook in Obsidian</summary><p>In Obsidian, choose “Open folder as vault” and select this folder. Save personal notes outside the Generated folder. Include a company ticker and workspace in the note’s frontmatter so the agents can find it. Link related notes with <code>[[COST]]</code>, then use Refresh memory after editing.</p><pre className="memory-note-template">{`---\ntitle: My COST thesis\nticker: COST\nnamespace: ${namespace}\n---\n# My COST thesis\n\nRelated company: [[COST]]\n\nMy view and questions to investigate…`}</pre><div><code>{graph.vault_path}</code><button type="button" className="button" onClick={async () => { try { await navigator.clipboard.writeText(graph.vault_path); setCopied(true); setCopyError('') } catch { setCopyError('Copy is unavailable. Select and copy the folder path above.') } }}>{copied ? 'Copied' : 'Copy folder path'}</button></div>{copyError && <p role="status">{copyError}</p>}<p className="muted-copy">Agent-managed notes are refreshed from the evidence archive. Keep your own writing in personal notes; it remains an opinion until supported by verified evidence.</p></details>
    </>}
    {(!graph || !graph.nodes.length) && selectedId && detail}
  </div>
}
