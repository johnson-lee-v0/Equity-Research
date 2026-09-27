import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

// Exercise the real reader and controls without requiring a graphics device.
// The scene itself is checked in-browser; these tests cover its HTML controls.
const require = createRequire(import.meta.url)
const { build } = createRequire(require.resolve('vite'))('esbuild')
const bundle = await build({
  entryPoints: [fileURLToPath(new URL('../src/panels/Memory.tsx', import.meta.url))],
  bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external', jsx: 'automatic',
  loader: { '.css': 'empty' }, logLevel: 'silent',
  plugins: [{
    name: 'scene-without-graphics',
    setup(build) {
      build.onResolve({ filter: /(?:^|\/)MemoryGraph3D(?:\.tsx)?$/ }, () => ({ path: 'scene', namespace: 'test-scene' }))
      build.onLoad({ filter: /.*/, namespace: 'test-scene' }, () => ({ contents: 'export default function MemoryGraph3D() { return null }', loader: 'js' }))
      build.onResolve({ filter: /\/api$/ }, () => ({ path: 'offline-memory-api', external: true }))
    },
  }],
})
const compiled = { exports: {} }
let controlledState = null
let readApi = () => assert.fail('Rendering memory must not dispatch an API request')
const componentReact = {
  ...React,
  useState(initial) {
    if (!controlledState) return React.useState(initial)
    const owner = controlledState, index = owner.cursor++
    if (index >= owner.values.length) owner.values.push(typeof initial === 'function' ? initial() : initial)
    return [owner.values[index], value => {
      owner.values[index] = typeof value === 'function' ? value(owner.values[index]) : value
    }]
  },
  useRef(initial) {
    if (!controlledState) return React.useRef(initial)
    const owner = controlledState, index = owner.cursor++
    if (index >= owner.values.length) owner.values.push({ current: initial })
    return owner.values[index]
  },
  useEffect(effect, dependencies) {
    if (!controlledState?.effects) return React.useEffect(effect, dependencies)
    controlledState.effects.push(effect)
  },
}
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(
  name => name === 'react' ? componentReact : name === 'offline-memory-api' ? { apiFetch: (...args) => readApi(...args) } : require(name),
  compiled, compiled.exports,
)
const { default: Memory, MemoryExplorer, MemoryNoteList, MemoryDetail } = compiled.exports

function render(component, props) {
  let tree
  function Capture() { tree = component(props); return tree }
  const html = renderToStaticMarkup(React.createElement(Capture))
  return { html, tree }
}

// Retain only the component's state between SSR passes. Real control handlers
// perform each update; this does not simulate browser effects, a DOM, or WebGL.
function interactive(component, initialProps) {
  const state = { values: [], cursor: 0 }
  let props = initialProps
  return (changes = {}) => {
    props = { ...props, ...changes }
    state.cursor = 0
    controlledState = state
    try { return render(component, props) }
    finally { controlledState = null }
  }
}

function memoryReader() {
  const state = { values: [], cursor: 0, effects: [] }
  return () => {
    state.cursor = 0
    state.effects = []
    controlledState = state
    try { return { ...render(Memory, { namespace: 'real' }), effects: [...state.effects] } }
    finally { controlledState = null }
  }
}

function deferred() {
  let resolve, reject
  const promise = new Promise((complete, fail) => { resolve = complete; reject = fail })
  return { promise, resolve, reject }
}

const settlePromises = () => new Promise(resolve => setImmediate(resolve))

function elements(tree, predicate) {
  if (!React.isValidElement(tree)) return []
  return [
    ...(predicate(tree) ? [tree] : []),
    ...React.Children.toArray(tree.props.children).flatMap(child => elements(child, predicate)),
  ]
}

function textOf(tree) {
  if (typeof tree === 'string' || typeof tree === 'number') return String(tree)
  return React.isValidElement(tree) ? React.Children.toArray(tree.props.children).map(textOf).join('') : ''
}

function button(tree, label) {
  const result = elements(tree, element => element.type === 'button'
    && (element.props['aria-label'] === label || textOf(element).trim() === label))[0]
  assert.ok(result, `Expected operable button: ${label}`)
  return result
}

const makeNode = (id, kind = 'fact') => ({
  id, title: `Title for ${id}`, kind, ticker: 'COST', path: `Generated/${id}.md`,
  updated_at: '2026-09-25T12:00:00Z', status: 'recorded', excerpt: `Excerpt for ${id}`,
})
const nodes = [makeNode('company', 'company'), makeNode('filing', 'source'), makeNode('capex')]
const graph = {
  version: 'test', vault_path: '/private/test-memory', nodes,
  edges: [{ source: 'company', target: 'filing', kind: 'wikilink' }, { source: 'capex', target: 'filing', kind: 'wikilink' }],
  tickers: ['COST'], kinds: ['company', 'source', 'fact'], total_nodes: 3, truncated: false,
}
const detailProps = {
  note: null, node: null, loading: false, error: '',
  onSelect: () => assert.fail('Rendering must not select or fetch a note'), onRetry: () => assert.fail('Rendering must not retry automatically'),
}

test('the accessible note list preserves the selected note and opens the exact requested identity', () => {
  const selected = []
  const { html, tree } = render(MemoryNoteList, { nodes, selectedId: 'filing', onSelect: id => selected.push(id) })
  const choices = elements(tree, element => element.type === 'button')
  assert.equal(choices.length, 3)
  assert.equal(choices.filter(choice => choice.props['aria-pressed'] === true).length, 1)
  assert.match(textOf(choices.find(choice => choice.props['aria-pressed'] === true)), /Title for filing/)
  assert.match(html, /Source/)
  assert.match(html, /COST/)
  assert.deepEqual(selected, [])
  choices.find(choice => textOf(choice).includes('Title for capex')).props.onClick()
  assert.deepEqual(selected, ['capex'])
})

test('a filtered list never presents an off-graph selection as another selected note', () => {
  const { tree } = render(MemoryNoteList, { nodes, selectedId: 'outside-filter', onSelect: () => {} })
  assert.ok(elements(tree, element => element.type === 'button').every(choice => choice.props['aria-pressed'] === false))
})

test('an unavailable connected note outside the graph exposes its failure and allows a targeted retry', () => {
  let retried = 0
  const { html, tree } = render(MemoryDetail, {
    ...detailProps, error: 'This memory note is unavailable in the selected workspace.', onRetry: () => retried++,
  })
  assert.match(html, /role="alert"/)
  assert.match(html, /aria-busy="false"/)
  assert.match(html, /This memory note is unavailable in the selected workspace/)
  assert.doesNotMatch(html, /Follow a connection/)
  assert.equal(retried, 0)
  button(tree, 'Try again').props.onClick()
  assert.equal(retried, 1)
})

test('an off-graph connected note shows loading before its metadata arrives', () => {
  const { html } = render(MemoryDetail, { ...detailProps, loading: true })
  assert.match(html, /aria-busy="true"/)
  assert.match(html, /role="status"/)
  assert.match(html, /Loading note/)
  assert.doesNotMatch(html, /Follow a connection|role="alert"/)
})

test('reading outside the current filters offers explicit reveal and previous-note actions', () => {
  let revealed = 0, returned = 0
  const note = { ...makeNode('outside-filter'), markdown: 'A linked fact.', frontmatter: {}, links: [] }
  const { html, tree } = render(MemoryDetail, {
    ...detailProps, note, onReveal: () => revealed++, onBack: () => returned++,
  })
  assert.match(html, /This note is outside the current map or filters/)
  button(tree, 'Find this note in the map').props.onClick()
  button(tree, '← Previous note').props.onClick()
  assert.equal(revealed, 1)
  assert.equal(returned, 1)
})

test('a loaded personal note stays an opinion and follows connections through the supplied reader', () => {
  const opened = []
  const note = {
    ...makeNode('personal', 'user_note'),
    markdown: '# My view\n\nA personal interpretation of [[filing|Annual filing]].',
    frontmatter: { source_url: 'https://investor.example.com/report' },
    links: [{ target: 'filing', label: 'Annual filing' }],
  }
  const { html, tree } = render(MemoryDetail, { ...detailProps, note, onSelect: id => opened.push(id) })
  assert.match(html, /interpretation or personal note/)
  assert.match(html, /href="https:\/\/investor.example.com\/report"/)
  assert.match(html, /rel="noopener noreferrer"/)
  button(tree, 'Annual filing').props.onClick()
  assert.deepEqual(opened, ['filing'])
})

test('the selected note reader never renders an executable or credential-bearing source link', () => {
  for (const source_url of ['javascript:alert(1)', 'https://name:secret@example.com/report']) {
    const note = { ...makeNode('unsafe'), markdown: 'Retained note text.', frontmatter: { source_url }, links: [] }
    const { html } = render(MemoryDetail, { ...detailProps, note })
    assert.doesNotMatch(html, /<a\b/)
    assert.match(html, /Retained note text/)
  }
})

test('3D exploration starts with discoverable camera controls and a separate readable list', () => {
  let read = 0
  const { tree } = render(MemoryExplorer, { graph, selectedId: 'filing', onSelect: () => {}, onRead: () => read++ })
  assert.equal(button(tree, '3D map').props['aria-pressed'], true)
  assert.equal(button(tree, 'List').props['aria-pressed'], false)
  for (const label of ['Reset view', 'Focus selected', 'Zoom in', 'Zoom out', 'Connections only']) button(tree, label)
  button(tree, 'Read selected note').props.onClick()
  assert.equal(read, 1)
})

test('camera focus is unavailable when selection is outside the loaded graph', () => {
  const { tree } = render(MemoryExplorer, { graph, selectedId: 'outside-filter', onSelect: () => {}, onRead: () => {} })
  assert.equal(button(tree, 'Focus selected').props.disabled, true)
  assert.equal(button(tree, 'Connections only').props.disabled, true)
})

const connectedGraph = {
  ...graph,
  nodes: [...nodes, makeNode('unrelated'), makeNode('other')],
  edges: [
    { source: 'company', target: 'filing', kind: 'wikilink' },
    { source: 'filing', target: 'capex', kind: 'wikilink' },
    { source: 'unrelated', target: 'other', kind: 'wikilink' },
  ],
  total_nodes: 5,
}

function renderedList(tree) {
  const list = elements(tree, element => element.type === MemoryNoteList)[0]
  assert.ok(list, 'The same accessible note list remains available')
  return list
}

test('Connections only follows incoming and outgoing links in List view without changing selection', () => {
  const selected = []
  const view = interactive(MemoryExplorer, { graph: connectedGraph, selectedId: 'filing', onSelect: id => selected.push(id), onRead: () => {} })
  let result = view()
  button(result.tree, 'Connections only').props.onClick()
  result = view()
  button(result.tree, 'List').props.onClick()
  result = view()
  assert.equal(button(result.tree, 'Connections only').props['aria-pressed'], true)
  assert.equal(button(result.tree, 'List').props['aria-pressed'], true)
  assert.deepEqual(renderedList(result.tree).props.nodes.map(node => node.id), ['company', 'filing', 'capex'])
  assert.equal(renderedList(result.tree).props.selectedId, 'filing')
  assert.match(result.html, /3 notes · 2 links · selected connections/)
  assert.doesNotMatch(result.html, /Title for unrelated|Title for other/)
  assert.deepEqual(selected, [], 'Changing view or connection scope never selects another note')
  button(result.tree, 'Connections only').props.onClick()
  result = view()
  assert.equal(renderedList(result.tree).props.nodes.length, 5)
  assert.equal(renderedList(result.tree).props.selectedId, 'filing')
})

test('following a note outside loaded filters cannot leave an isolated explorer blank', () => {
  const view = interactive(MemoryExplorer, { graph: connectedGraph, selectedId: 'filing', onSelect: () => {}, onRead: () => {} })
  let result = view()
  button(result.tree, 'Connections only').props.onClick()
  button(result.tree, 'List').props.onClick()
  result = view({ selectedId: 'outside-filter' })
  assert.deepEqual(renderedList(result.tree).props.nodes.map(node => node.id), connectedGraph.nodes.map(node => node.id))
  assert.equal(renderedList(result.tree).props.selectedId, 'outside-filter')
  assert.equal(button(result.tree, 'Connections only').props.disabled, true)
  assert.equal(button(result.tree, 'Connections only').props['aria-pressed'], false)
  assert.match(result.html, /Reading a note outside this view/)
  assert.match(result.html, /5 notes · 3 links/)
})

test('a 3D availability failure switches to an accessible list and preserves the reader selection', () => {
  const opened = []
  const view = interactive(MemoryExplorer, { graph: connectedGraph, selectedId: 'filing', onSelect: id => opened.push(id), onRead: () => {} })
  let result = view()
  const scene = elements(result.tree, element => typeof element.props.onUnavailable === 'function')[0]
  assert.ok(scene, 'The lazy 3D scene receives an availability callback')
  scene.props.onUnavailable()
  result = view()
  assert.match(result.html, /3D is unavailable on this device/)
  assert.match(result.html, /role="status"/)
  assert.equal(button(result.tree, '3D map').props.disabled, true)
  assert.equal(button(result.tree, 'List').props['aria-pressed'], true)
  const list = renderedList(result.tree)
  assert.deepEqual(list.props.nodes.map(node => node.id), connectedGraph.nodes.map(node => node.id))
  assert.equal(list.props.selectedId, 'filing')
  list.props.onSelect('capex')
  assert.deepEqual(opened, ['capex'])
  assert.equal(elements(result.tree, element => typeof element.props.onUnavailable === 'function').length, 0)
})

for (const outcome of ['matching notes', 'no matches', 'error']) {
  test(`a pending filter returning ${outcome} cannot overwrite or hide a newer reader choice`, async () => {
    const previousWindow = globalThis.window
    const timers = new Map(), cleanups = []
    let timerId = 0
    globalThis.window = {
      setTimeout(callback) { timers.set(++timerId, callback); return timerId },
      clearTimeout(id) { timers.delete(id) },
    }
    const flushTimers = () => { for (const [id, callback] of timers) { timers.delete(id); callback() } }
    const pendingFilter = deferred()
    const outside = {
      ...makeNode('outside-filter'), markdown: 'The newly chosen note stays readable.', frontmatter: {}, links: [],
    }
    const reads = []
    readApi = (address, options, controls) => {
      const url = new URL(address, 'http://localhost')
      reads.push(url.pathname)
      assert.equal(url.searchParams.get('namespace'), 'real')
      assert.equal(options?.method, undefined, 'Exploring memory may only read saved information')
      assert.ok(controls.signal instanceof AbortSignal)
      if (url.pathname === '/api/memory/graph') return url.searchParams.get('query') ? pendingFilter.promise : Promise.resolve(graph)
      if (url.pathname === '/api/memory/notes/company') return Promise.resolve({ ...nodes[0], markdown: 'Company overview.', frontmatter: {}, links: [{ target: outside.id, label: outside.title }] })
      if (url.pathname === '/api/memory/notes/outside-filter') return Promise.resolve(outside)
      assert.fail(`Unexpected memory read: ${url.pathname}`)
    }
    try {
      const view = memoryReader()
      let result = view()
      cleanups.push(result.effects[0]())
      flushTimers()
      await settlePromises()
      result = view()
      cleanups.push(result.effects[1]())
      await settlePromises()
      result = view()
      const search = elements(result.tree, element => element.type === 'input' && element.props.type === 'search')[0]
      search.props.onChange({ target: { value: 'new filter' } })
      result = view()
      cleanups.push(result.effects[0]())
      flushTimers()
      // The previous graph remains visible during this request. The user follows
      // a connection before that older filter response has settled.
      const reader = elements(result.tree, element => element.type === MemoryDetail)[0]
      reader.props.onSelect(outside.id)
      result = view()
      cleanups.push(result.effects[1]())
      await settlePromises()
      if (outcome === 'error') pendingFilter.reject(new Error('The filtered graph could not load.'))
      else pendingFilter.resolve({ ...graph, nodes: outcome === 'no matches' ? [] : [makeNode('filtered-company', 'company')], edges: [], total_nodes: outcome === 'no matches' ? 0 : 1 })
      await settlePromises()
      result = view()
      const retained = elements(result.tree, element => element.type === MemoryDetail)[0]
      assert.ok(retained, 'A graph response cannot remove the independently selected reader')
      assert.equal(retained.props.note?.id, outside.id)
      assert.equal(retained.props.loading, false)
      assert.match(result.html, /The newly chosen note stays readable/)
      assert.ok(reads.includes('/api/memory/notes/outside-filter'))
      if (outcome === 'no matches') assert.match(result.html, /No notes match these filters/)
      if (outcome === 'error') assert.match(result.html, /The filtered graph could not load/)
    } finally {
      cleanups.filter(Boolean).forEach(cleanup => cleanup())
      if (previousWindow === undefined) delete globalThis.window
      else globalThis.window = previousWindow
      readApi = () => assert.fail('Rendering memory must not dispatch an API request')
    }
  })
}
