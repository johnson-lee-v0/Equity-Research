export interface SharedMemoryNode {
  id: string
  title: string
  kind: string
  ticker: string | null
  path: string
  updated_at: string | null
  status: string
  excerpt: string
  source_url?: string | null
  source_refs?: string[]
}

export interface SharedMemoryEdge {
  source: string
  target: string
  kind: string
}

export interface SharedMemoryGraph {
  version: string
  vault_path: string
  nodes: SharedMemoryNode[]
  edges: SharedMemoryEdge[]
  tickers: string[]
  kinds: string[]
  total_nodes: number
  truncated: boolean
}

export interface SharedMemoryNote extends SharedMemoryNode {
  markdown: string
  frontmatter: Record<string, unknown>
  links: Array<{ target: string; label: string }>
}

export const MEMORY_KINDS: Record<string, { label: string; color: string; description: string }> = {
  company: { label: 'Company', color: '#e3c98f', description: 'The company overview and its connected research.' },
  source: { label: 'Source', color: '#8ccfe8', description: 'An archived document with its original source.' },
  fact: { label: 'Fact', color: '#a6dcb2', description: 'A recorded observation with evidence and reporting period.' },
  earnings: { label: 'Earnings review', color: '#aebff0', description: 'A saved earnings interpretation linked to its original materials.' },
  opinion: { label: 'Analysis', color: '#cab3f1', description: 'An agent’s interpretation or decision.' },
  gap: { label: 'Open question', color: '#f0ae93', description: 'Something the research still needs to establish.' },
  user_note: { label: 'Your note', color: '#e7b6d1', description: 'A personal note; treated as an opinion until verified.' },
}

export function memoryKind(kind: string) {
  return MEMORY_KINDS[kind] ?? { label: kind.replaceAll('_', ' '), color: '#bdc8c4', description: 'A linked research note.' }
}

export function safeMemoryUrl(value: unknown): string | null {
  if (typeof value !== 'string') return null
  try {
    const url = new URL(value)
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null
  } catch { return null }
}

export function memoryGraphQuery(namespace: string, filters: { ticker: string; kind: string; query: string }) {
  const params = new URLSearchParams({ namespace, limit: '300' })
  if (filters.ticker) params.set('ticker', filters.ticker)
  if (filters.kind) params.set('kind', filters.kind)
  if (filters.query.trim()) params.set('query', filters.query.trim())
  return `/api/memory/graph?${params}`
}

export function linkedMemoryNodes(id: string | null, edges: SharedMemoryEdge[]): Set<string> {
  const result = new Set<string>()
  if (!id) return result
  result.add(id)
  for (const edge of edges) {
    if (edge.source === id) result.add(edge.target)
    if (edge.target === id) result.add(edge.source)
  }
  return result
}

export interface PositionedMemoryNode extends SharedMemoryNode { x: number; y: number; radius: number }

export interface PositionedMemoryNode3D extends PositionedMemoryNode { z: number }

type MemoryCell = { x: number; y: number; z: number }

const MEMORY_CELL_SIZE = 24
const MEMORY_KIND_POSITION: Record<string, MemoryCell> = {
  company: { x: 0, y: 0, z: 0 },
  source: { x: -1, y: 1, z: 1 },
  fact: { x: 1, y: 1, z: 1 },
  earnings: { x: 0, y: 1, z: -1 },
  opinion: { x: 0, y: -1, z: -1 },
  gap: { x: 1, y: -1, z: 1 },
  user_note: { x: -1, y: -1, z: 1 },
}

function memoryCellOffsets(count: number): MemoryCell[] {
  const radius = Math.ceil(Math.cbrt(Math.max(1, count)))
  const offsets: MemoryCell[] = []
  for (let x = -radius; x <= radius; x++) {
    for (let y = -radius; y <= radius; y++) {
      for (let z = -radius; z <= radius; z++) offsets.push({ x, y, z })
    }
  }
  return offsets.sort((a, b) => (a.x ** 2 + a.y ** 2 + a.z ** 2) - (b.x ** 2 + b.y ** 2 + b.z ** 2) || a.x - b.x || a.y - b.y || a.z - b.z).slice(0, count)
}

function memoryNodeJitter(id: string, axis: number): number {
  let hash = 2166136261 + axis
  for (let index = 0; index < id.length; index++) hash = Math.imul(hash ^ id.charCodeAt(index), 16777619)
  return ((hash >>> 0) % 1000 / 999 - .5) * 2
}

/**
 * Company clouds contain category clusters and only the supplied note identities.
 * Actual links gently bring related notes closer; a fixed lattice keeps every
 * sphere separate without a moving simulation or selection-dependent positions.
 */
export function layoutMemoryGraph3D(nodes: SharedMemoryNode[], edges: SharedMemoryEdge[]): PositionedMemoryNode3D[] {
  if (!nodes.length) return []
  const sorted = [...nodes].sort((a, b) => (a.ticker ?? '').localeCompare(b.ticker ?? '') || a.kind.localeCompare(b.kind) || a.id.localeCompare(b.id) || JSON.stringify(a).localeCompare(JSON.stringify(b)))
  const unique = [...new Map(sorted.map((node) => [node.id, node])).values()]
  const byId = new Map(unique.map((node) => [node.id, node]))
  const groupKey = (node: SharedMemoryNode) => node.ticker ? `ticker:${node.ticker}` : 'shared:'
  const neighbors = new Map(unique.map((node) => [node.id, new Set<string>()]))
  for (const edge of edges) {
    if (edge.source === edge.target || !byId.has(edge.source) || !byId.has(edge.target)) continue
    neighbors.get(edge.source)!.add(edge.target)
    neighbors.get(edge.target)!.add(edge.source)
  }
  const groups = new Map<string, SharedMemoryNode[]>()
  for (const node of unique) {
    const key = groupKey(node)
    groups.set(key, [...(groups.get(key) ?? []), node])
  }
  const offsets = memoryCellOffsets(unique.length + 1)
  const localPositions = new Map<string, MemoryCell>()
  let halfExtent = 0
  for (const peers of groups.values()) {
    const categoryKey = (node: SharedMemoryNode) => Object.hasOwn(MEMORY_KIND_POSITION, node.kind) ? node.kind : 'other'
    const categoryCounts = new Map<string, number>()
    for (const node of peers) categoryCounts.set(categoryKey(node), (categoryCounts.get(categoryKey(node)) ?? 0) + 1)
    const spread = Math.max(2, Math.ceil(Math.cbrt(Math.max(...categoryCounts.values())) / 2) + 1)
    const ordered = [...peers].sort((a, b) => Number(b.kind === 'company') - Number(a.kind === 'company') || a.kind.localeCompare(b.kind) || neighbors.get(b.id)!.size - neighbors.get(a.id)!.size || a.id.localeCompare(b.id))
    const categoryIndex = new Map<string, number>()
    const seeds = new Map<string, MemoryCell>()
    for (const node of ordered) {
      const kind = categoryKey(node)
      const index = categoryIndex.get(kind) ?? 0
      categoryIndex.set(kind, index + 1)
      const center = MEMORY_KIND_POSITION[kind] ?? { x: 0, y: 0, z: 2 }
      const offset = offsets[index]
      seeds.set(node.id, { x: center.x * spread + offset.x, y: center.y * spread + offset.y, z: center.z * spread + offset.z })
    }
    const occupied = new Set<string>()
    const anchorId = ordered.find((node) => node.kind === 'company')?.id
    for (const node of ordered) {
      const seed = seeds.get(node.id)!
      const linked = [...neighbors.get(node.id)!].sort().filter((id) => groupKey(byId.get(id)!) === groupKey(node))
      let desired = seed
      if (linked.length && node.id !== anchorId) {
        const average = linked.reduce((total, id) => {
          const neighbor = seeds.get(id)!
          return { x: total.x + neighbor.x / linked.length, y: total.y + neighbor.y / linked.length, z: total.z + neighbor.z / linked.length }
        }, { x: 0, y: 0, z: 0 })
        const delta = { x: average.x - seed.x, y: average.y - seed.y, z: average.z - seed.z }
        const strength = Math.min(.2, 1 / Math.max(1, Math.hypot(delta.x, delta.y, delta.z)))
        desired = { x: seed.x + delta.x * strength, y: seed.y + delta.y * strength, z: seed.z + delta.z * strength }
      }
      const nearest = { x: Math.round(desired.x), y: Math.round(desired.y), z: Math.round(desired.z) }
      // At most peers.length cells can be occupied, so this search always ends.
      for (const offset of offsets) {
        const cell = { x: nearest.x + offset.x, y: nearest.y + offset.y, z: nearest.z + offset.z }
        const key = `${cell.x},${cell.y},${cell.z}`
        if (occupied.has(key)) continue
        occupied.add(key)
        localPositions.set(node.id, cell)
        halfExtent = Math.max(halfExtent, Math.abs(cell.x), Math.abs(cell.y), Math.abs(cell.z))
        break
      }
    }
  }
  const columns = Math.ceil(Math.cbrt(groups.size))
  const stride = (halfExtent * 2 + 5) * MEMORY_CELL_SIZE
  const centers = new Map([...groups.keys()].map((key, index) => [key, {
    x: index % columns * stride,
    y: Math.floor(index / columns) % columns * stride,
    z: Math.floor(index / (columns * columns)) * stride,
  }]))
  const result = unique.map((node) => {
    const cell = localPositions.get(node.id)!, center = centers.get(groupKey(node))!
    return { ...node,
      x: center.x + cell.x * MEMORY_CELL_SIZE + memoryNodeJitter(node.id, 0),
      y: center.y + cell.y * MEMORY_CELL_SIZE + memoryNodeJitter(node.id, 1),
      z: center.z + cell.z * MEMORY_CELL_SIZE + memoryNodeJitter(node.id, 2),
      radius: node.kind === 'company' ? 9 : node.kind === 'source' ? 6 : 5,
    }
  })
  const middle = (axis: 'x' | 'y' | 'z') => (Math.min(...result.map((node) => node[axis])) + Math.max(...result.map((node) => node[axis]))) / 2
  const center = { x: middle('x'), y: middle('y'), z: middle('z') }
  return result.map((node) => ({ ...node, x: node.x - center.x, y: node.y - center.y, z: node.z - center.z }))
}

/** Stable, bounded layout: cluster by ticker, then pull actual linked notes together. */
export function layoutMemoryGraph(nodes: SharedMemoryNode[], edges: SharedMemoryEdge[]): PositionedMemoryNode[] {
  if (!nodes.length) return []
  const sorted = [...nodes].sort((a, b) => (a.ticker ?? '').localeCompare(b.ticker ?? '') || a.kind.localeCompare(b.kind) || a.id.localeCompare(b.id))
  const groups = [...new Set(sorted.map((node) => node.ticker || 'Shared'))]
  const groupPositions = new Map(groups.map((group, index) => {
    const angle = index * Math.PI * 2 / groups.length - Math.PI / 2
    return [group, { x: 550 + (groups.length > 1 ? 265 * Math.cos(angle) : 0), y: 315 + (groups.length > 1 ? 155 * Math.sin(angle) : 0) }]
  }))
  const positions = sorted.map((node) => {
    const peers = sorted.filter((candidate) => candidate.ticker === node.ticker)
    const index = peers.findIndex((candidate) => candidate.id === node.id)
    const center = groupPositions.get(node.ticker || 'Shared')!
    const angle = index * 2.399963229728653
    const distance = node.kind === 'company' ? 0 : 35 + 12 * Math.sqrt(index + 1)
    return { ...node, x: center.x + distance * Math.cos(angle), y: center.y + distance * Math.sin(angle), radius: node.kind === 'company' ? 13 : node.kind === 'source' ? 8 : 6 }
  })
  const indexById = new Map(positions.map((node, index) => [node.id, index]))
  const validEdges = edges.flatMap((edge) => {
    const source = indexById.get(edge.source), target = indexById.get(edge.target)
    return source == null || target == null || source === target ? [] : [[source, target]]
  })
  for (let iteration = 0; iteration < 90; iteration++) {
    const forces = positions.map(() => ({ x: 0, y: 0 }))
    for (let i = 0; i < positions.length; i++) {
      for (let j = i + 1; j < positions.length; j++) {
        let dx = positions[i].x - positions[j].x, dy = positions[i].y - positions[j].y
        if (dx === 0 && dy === 0) { dx = 1; dy = .5 }
        const distance = Math.max(3, Math.hypot(dx, dy))
        const repulsion = Math.min(8, 125 / (distance * distance))
        forces[i].x += dx * repulsion; forces[i].y += dy * repulsion
        forces[j].x -= dx * repulsion; forces[j].y -= dy * repulsion
      }
    }
    for (const [source, target] of validEdges) {
      const dx = positions[target].x - positions[source].x, dy = positions[target].y - positions[source].y
      const distance = Math.max(1, Math.hypot(dx, dy))
      const strength = (distance - 70) * .025
      forces[source].x += dx / distance * strength; forces[source].y += dy / distance * strength
      forces[target].x -= dx / distance * strength; forces[target].y -= dy / distance * strength
    }
    positions.forEach((node, index) => {
      const center = groupPositions.get(node.ticker || 'Shared')!
      const anchor = node.kind === 'company' ? .065 : .008
      node.x = Math.max(55, Math.min(1045, node.x + Math.max(-10, Math.min(10, forces[index].x + (center.x - node.x) * anchor))))
      node.y = Math.max(45, Math.min(555, node.y + Math.max(-10, Math.min(10, forces[index].y + (center.y - node.y) * anchor))))
    })
  }
  return positions
}

export function memoryBody(markdown: string): string {
  return markdown.replace(/^\uFEFF?---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/, '').trim()
}
