import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'
async function moduleAt(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}
const { layoutMemoryGraph, layoutMemoryGraph3D, linkedMemoryNodes, safeMemoryUrl, memoryGraphQuery, memoryBody } = await moduleAt('../src/panels/memoryGraphModel.ts')
const { modelEfforts } = await moduleAt('../src/modelOptions.ts')
const node = (id, kind = 'fact', ticker = 'COST') => ({ id, title: id, kind, ticker, path: `${id}.md`, updated_at: null, status: 'recorded', excerpt: '' })

test('server filters preserve namespace, escape special characters and omit empty filters', () => {
  const url = new URL(memoryGraphQuery('real', { ticker: 'BRK.B', kind: 'gap', query: ' capex & growth ' }), 'http://localhost')
  assert.equal(url.pathname, '/api/memory/graph')
  assert.equal(url.searchParams.get('namespace'), 'real')
  assert.equal(url.searchParams.get('ticker'), 'BRK.B')
  assert.equal(url.searchParams.get('query'), 'capex & growth')
  assert.equal(url.searchParams.get('kind'), 'gap')
  assert.equal(url.searchParams.get('limit'), '300')
  const empty = new URL(memoryGraphQuery('demo', { ticker: '', kind: '', query: ' ' }), 'http://localhost')
  assert.equal(empty.searchParams.get('namespace'), 'demo')
  assert.equal(empty.searchParams.has('query'), false)
})

test('graph preserves source identities, is stable across response order and ignores absent endpoints', () => {
  const nodes = [node('cost', 'company'), node('call', 'source'), node('revenue'), node('nke', 'company', 'NKE')]
  const edges = [{ source: 'cost', target: 'call', kind: 'wikilink' }, { source: 'call', target: 'revenue', kind: 'wikilink' }]
  const before = structuredClone(nodes)
  const first = layoutMemoryGraph(nodes, edges)
  assert.deepEqual(layoutMemoryGraph(nodes.toReversed(), edges), first)
  assert.deepEqual(layoutMemoryGraph(nodes, [...edges, { source: 'missing', target: 'cost', kind: 'wikilink' }]), first)
  assert.deepEqual(nodes, before)
  assert.equal(first.length, nodes.length)
  assert.deepEqual(new Set(first.map((item) => item.id)), new Set(nodes.map((item) => item.id)))
  for (const result of first) { assert.ok(Number.isFinite(result.x) && Number.isFinite(result.y)); assert.ok(result.x >= 55 && result.x <= 1045); assert.ok(result.y >= 45 && result.y <= 555) }
  assert.notDeepEqual(layoutMemoryGraph(nodes, []), first, 'Actual evidence links affect the layout')
})

test('selection highlights incoming and outgoing relationships without unrelated notes', () => {
  const edges = [{ source: 'company', target: 'source' }, { source: 'fact', target: 'source' }, { source: 'other', target: 'another' }]
  assert.deepEqual([...linkedMemoryNodes('source', edges)].sort(), ['company', 'fact', 'source'])
  assert.equal(linkedMemoryNodes(null, edges).size, 0)
})

test('large or empty graphs have a finite bounded layout', () => {
  assert.deepEqual(layoutMemoryGraph([], []), [])
  const nodes = Array.from({ length: 300 }, (_, index) => node(`note-${index}`, index % 15 === 0 ? 'company' : 'fact', `T${index % 8}`))
  const result = layoutMemoryGraph(nodes, nodes.slice(1).map((item) => ({ source: nodes[0].id, target: item.id, kind: 'wikilink' })))
  assert.equal(result.length, 300)
  assert.ok(result.every((item) => Number.isFinite(item.x) && Number.isFinite(item.y)))
})

test('3D positions are reproducible across node order, edge order and duplicate links', () => {
  const nodes = [node('cost', 'company'), node('call', 'source'), node('revenue'), node('gap', 'gap'), node('nke', 'company', 'NKE'), node('nke-fact', 'fact', 'NKE')]
  const edges = [{ source: 'cost', target: 'call', kind: 'wikilink' }, { source: 'call', target: 'revenue', kind: 'wikilink' }, { source: 'revenue', target: 'gap', kind: 'wikilink' }]
  const original = structuredClone({ nodes, edges })
  const result = layoutMemoryGraph3D(nodes, edges)
  assert.deepEqual(layoutMemoryGraph3D(nodes.toReversed(), edges.toReversed()), result)
  assert.deepEqual(layoutMemoryGraph3D(nodes, [...edges, edges[0], { source: 'call', target: 'cost', kind: 'wikilink' }, { source: 'missing', target: 'cost', kind: 'wikilink' }, { source: 'cost', target: 'cost', kind: 'wikilink' }]), result)
  assert.deepEqual({ nodes, edges }, original, 'Layout must not change archived note data or relationships')
  assert.deepEqual(new Set(result.map((item) => item.id)), new Set(nodes.map((item) => item.id)))
  assert.notDeepEqual(layoutMemoryGraph3D(nodes, []), result, 'Verified relationships influence note placement')
})

test('3D category clusters have visible depth and stay near their own company', () => {
  const nodes = ['COST', 'NKE'].flatMap((ticker) => ['company', 'source', 'fact', 'earnings', 'opinion', 'gap', 'user_note'].map((kind) => node(`${ticker}-${kind}`, kind, ticker)))
  const result = layoutMemoryGraph3D(nodes, [])
  const company = (ticker) => result.find((item) => item.ticker === ticker && item.kind === 'company')
  const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z)
  for (const item of result) {
    const own = company(item.ticker), other = company(item.ticker === 'COST' ? 'NKE' : 'COST')
    assert.ok(distance(item, own) < distance(item, other), `${item.id} belongs near its company's anchor`)
  }
  assert.ok(new Set(result.filter((item) => item.ticker === 'COST').map((item) => Math.round(item.z / 24))).size >= 3)
})

test('3D layout retains disconnected notes and handles missing company anchors and unusual categories', () => {
  assert.deepEqual(layoutMemoryGraph3D([], []), [])
  const nodes = [node('personal', 'user_note', null), node('unclassified', '__proto__', null), node('orphan', 'fact', 'BRK.B')]
  const result = layoutMemoryGraph3D(nodes, [{ source: 'orphan', target: 'not-in-response', kind: 'wikilink' }])
  assert.equal(result.length, 3, 'Layout must not invent a company anchor or missing linked note')
  assert.ok(result.every((item) => [item.x, item.y, item.z, item.radius].every(Number.isFinite)))
  assert.deepEqual(layoutMemoryGraph3D([nodes[0], nodes[0]], []), layoutMemoryGraph3D([nodes[0]], []), 'A repeated note identity appears once')
})

test('3D layout separates every sphere at the 1,000-note API limit', () => {
  for (const tickerCount of [1, 8, 1000]) {
    const nodes = Array.from({ length: 1000 }, (_, index) => node(`note-${index}`, index % 15 === 0 ? 'company' : 'fact', `T${index % tickerCount}`))
    const edges = nodes.slice(1).map((item) => ({ source: nodes[0].id, target: item.id, kind: 'wikilink' }))
    const result = layoutMemoryGraph3D(nodes, edges)
    assert.equal(result.length, 1000)
    for (let index = 0; index < result.length; index++) {
      const item = result[index]
      assert.ok([item.x, item.y, item.z, item.radius].every(Number.isFinite))
      assert.ok(Math.max(Math.abs(item.x), Math.abs(item.y), Math.abs(item.z)) < 10000)
      for (const other of result.slice(index + 1)) {
        assert.ok(Math.hypot(item.x - other.x, item.y - other.y, item.z - other.z) > item.radius + other.radius, `${item.id} and ${other.id} must not overlap`)
      }
    }
  }
})

test('source links reject executable URLs and embedded credentials', () => {
  for (const value of ['javascript:alert(1)', 'data:text/html,hello', '//example.com', 'https://user:password@example.com', 'file:///tmp/note.md', undefined]) assert.equal(safeMemoryUrl(value), null)
  assert.equal(safeMemoryUrl('https://investor.example.com/earnings'), 'https://investor.example.com/earnings')
})

test('note body removes only leading frontmatter and keeps the actual note text', () => {
  assert.equal(memoryBody('---\nkind: fact\nticker: COST\n---\n# Sales\n\n- Revenue grew.'), '# Sales\n\n- Revenue grew.')
  assert.equal(memoryBody('# Note\n---\nText'), '# Note\n---\nText')
})

test('current model effort fallbacks include xhigh and enforce Luna maximum', () => {
  assert.deepEqual(modelEfforts('gpt-6-luna'), ['low', 'medium', 'high', 'xhigh', 'max'])
  for (const model of ['gpt-6-sol', 'gpt-6-astra']) assert.deepEqual(modelEfforts(model), ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'])
  assert.equal(modelEfforts('gpt-5.6-luna').includes('ultra'), false)
})
