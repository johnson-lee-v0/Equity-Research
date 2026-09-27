import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const require = createRequire(import.meta.url)
const { build } = createRequire(require.resolve('vite'))('esbuild')
const bundle = await build({
  entryPoints: [fileURLToPath(new URL('../src/panels/research/EarningsWorkflow.tsx', import.meta.url))],
  bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external', jsx: 'automatic',
  loader: { '.css': 'empty' }, logLevel: 'silent',
  plugins: [{ name: 'offline-api', setup(build) {
    build.onResolve({ filter: /\/api$/ }, () => ({ path: 'offline-api', external: true }))
  } }],
})

// Render the real component with controlled hook state and a deferred API read.
// Effects run only when requested, so a response can arrive before React's
// previous-effect cleanup—the small race this regression needs to exercise.
let slots, cursor, effects, read
const hooks = {
  ...React,
  useState(initial) {
    const index = cursor++
    if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial
    return [slots[index], value => { slots[index] = typeof value === 'function' ? value(slots[index]) : value }]
  },
  useRef(initial) {
    const index = cursor++
    if (!(index in slots)) slots[index] = { current: initial }
    return slots[index]
  },
  useEffect(effect) { effects.push(effect) },
}
const compiled = { exports: {} }
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(
  name => name === 'react' ? hooks : name === 'offline-api' ? { apiFetch: (...args) => read(...args), ApiError: class extends Error {} } : require(name),
  compiled, compiled.exports,
)
const EarningsWorkflow = compiled.exports.default

function workflow(id, extra = {}) {
  return { id, workflow: 'earnings', version: 'earnings.v2', ticker: 'META', status: 'completed', created_at: '2026-09-25', steps: [], result: { documents: {}, trends: { series: [] }, source_ids: ['saved-source'] }, ...extra }
}

function setup(selected, history = [selected]) {
  // Initial selected/ticker/history are the state after a successful saved read.
  slots = ['META', history, selected.id, selected, null, '', '', '', '', 0, undefined, 0]
  read = () => assert.fail('This render must not dispatch a request')
  return () => {
    cursor = 0
    effects = []
    const tree = EarningsWorkflow({ initialWorkflowId: 'original', embedded: true })
    return { tree, html: renderToStaticMarkup(tree), effects: [...effects] }
  }
}

function button(tree, label) {
  if (!React.isValidElement(tree)) return undefined
  if (tree.type === 'button' && React.Children.toArray(tree.props.children).includes(label)) return tree
  for (const child of React.Children.toArray(tree.props.children)) {
    const found = button(child, label)
    if (found) return found
  }
}

test('embedded review keeps the original selected until updated figures are explicitly opened', () => {
  const original = workflow('original')
  const updated = workflow('revision', { source_refresh_of: 'original', created_at: '2026-09-26' })
  const { html, tree } = setup(original, [updated, original])()
  assert.match(html, /Earnings used in this assessment/)
  assert.ok(button(tree, 'Open updated figures'))
  assert.doesNotMatch(html, /Updated earnings coverage|Return to assessment evidence/)
})

test('a source revision and a new earnings event are both distinguished from the assessment evidence', () => {
  const revision = setup(workflow('revision', { source_refresh_of: 'original' }))()
  assert.match(revision.html, /Updated earnings coverage/)
  assert.match(revision.html, /separate source-coverage revision/)
  assert.doesNotMatch(revision.html, /Earnings used in this assessment/)
  assert.ok(button(revision.tree, 'Return to assessment evidence'))
  const nextEvent = setup(workflow('new-event'))()
  assert.match(nextEvent.html, /Separate earnings review/)
  assert.doesNotMatch(nextEvent.html, /Earnings used in this assessment|separate source-coverage revision/)
  assert.ok(button(nextEvent.tree, 'Return to assessment evidence'))
})

test('updating figures uses the targeted endpoint and preserves the original assessment review', async () => {
  const original = workflow('original')
  const saved = structuredClone(original)
  const revision = workflow('revision', { source_refresh_of: 'original' })
  const render = setup(original)
  const view = render()
  read = async (url, options, controls) => {
    assert.equal(url, '/api/research-workflows/runs/original/refresh-sources')
    assert.equal(options.method, 'POST')
    assert.equal(controls.mutation, true)
    return revision
  }
  const previousWindow = globalThis.window
  globalThis.window = { location: { href: 'http://localhost/#research' } }
  try {
    button(view.tree, 'Update missing figures').props.onClick()
    await Promise.resolve()
    const updated = render()
    assert.match(updated.html, /Updated earnings coverage/)
    assert.doesNotMatch(updated.html, /Earnings used in this assessment/)
    assert.deepEqual(original, saved)
    button(updated.tree, 'Return to assessment evidence').props.onClick()
    assert.match(render().html, /Loading saved workflow/)
  } finally {
    if (previousWindow === undefined) delete globalThis.window
    else globalThis.window = previousWindow
  }
})

for (const response of ['success', 'error']) {
  test(`returning to original evidence ignores an old poll ${response} even before effect cleanup`, async () => {
    const previousWindow = globalThis.window
    globalThis.window = { clearTimeout }
    try {
      const revision = workflow('revision', { source_refresh_of: 'original' })
      const render = setup(revision)
      const view = render()
      let complete, fail
      read = url => {
        assert.equal(url, '/api/research-workflows/runs/revision')
        return new Promise((resolve, reject) => { complete = resolve; fail = reject })
      }
      const cleanup = view.effects[1]()
      button(view.tree, 'Return to assessment evidence').props.onClick()
      if (response === 'success') complete(revision)
      else fail(new Error('Stale revision read failed'))
      await Promise.resolve()
      const next = render()
      assert.match(next.html, /Loading saved workflow/)
      assert.doesNotMatch(next.html, /Updated earnings coverage|Stale revision read failed/)
      cleanup()
    } finally {
      if (previousWindow === undefined) delete globalThis.window
      else globalThis.window = previousWindow
    }
  })
}

test('source refresh is unavailable while a workflow or another action is active', () => {
  const active = setup(workflow('original', { status: 'running' }))()
  assert.equal(button(active.tree, 'Update missing figures'), undefined)
  assert.equal(button(active.tree, 'Refresh materials & trends'), undefined)
  const render = setup(workflow('original'))
  slots[5] = 'refresh-sources'
  const updating = render()
  assert.equal(button(updating.tree, 'Updating missing figures…').props.disabled, true)
  assert.equal(button(updating.tree, 'Refresh materials & trends').props.disabled, true)
})
