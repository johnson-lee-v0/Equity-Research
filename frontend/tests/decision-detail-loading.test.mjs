import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

// Bundle the actual component and its local imports; use Vite's existing
// esbuild dependency and the same React instance as the server renderer.
const require = createRequire(import.meta.url)
const { build } = createRequire(require.resolve('vite'))('esbuild')
const bundle = await build({
  entryPoints: [fileURLToPath(new URL('../src/panels/DecisionWorkspace.tsx', import.meta.url))],
  bundle: true,
  write: false,
  platform: 'node',
  format: 'cjs',
  packages: 'external',
  jsx: 'automatic',
  loader: { '.css': 'empty' },
  logLevel: 'silent',
})
const compiled = { exports: {} }
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(require, compiled, compiled.exports)
const { DecisionWorkspace } = compiled.exports

function renderWorkspace(props) {
  let tree
  function CaptureWorkspace() {
    tree = DecisionWorkspace(props)
    return tree
  }
  const html = renderToStaticMarkup(React.createElement(CaptureWorkspace))
  return { html, tree }
}

function findButton(tree, label) {
  if (!React.isValidElement(tree)) return undefined
  if (tree.type === 'button' && React.Children.toArray(tree.props.children).includes(label)) return tree
  for (const child of React.Children.toArray(tree.props.children)) {
    const button = findButton(child, label)
    if (button) return button
  }
}

function workspaceProps(mode, status) {
  return {
    mode,
    namespace: 'real',
    runs: [],
    selectedRun: null,
    selectedRunId: 'run-meta',
    detailLoad: { runId: 'run-meta', status, error: status === 'error' ? 'The saved case request timed out.' : null },
    sources: [],
    onSelectRun: () => assert.fail('Rendering must not fetch details automatically'),
    onClearRun: () => {},
    onOpenSource: () => {},
    onNavigate: () => assert.fail('Opening saved details must not navigate elsewhere'),
    onControl: () => assert.fail('Opening saved details must not dispatch research'),
    onDispatchReddit: () => assert.fail('Opening saved details must not dispatch research'),
  }
}

for (const mode of ['research', 'watchlist']) {
  const backLabel = `Back to ${mode}`

  test(`${mode}: opening a card immediately shows loading and an operable back button`, () => {
    let cleared = 0
    const { html, tree } = renderWorkspace({ ...workspaceProps(mode, 'loading'), onClearRun: () => cleared++ })
    assert.match(html, /aria-busy="true"/)
    assert.match(html, /role="status"/)
    assert.match(html, /Opening saved research…/)
    assert.match(html, new RegExp(backLabel))
    assert.doesNotMatch(html, /Try again|role="alert"/)
    const back = findButton(tree, backLabel)
    assert.ok(back, 'The loading screen offers the correct return action')
    back.props.onClick()
    assert.equal(cleared, 1)
  })

  test(`${mode}: a failed detail request retries only the selected saved run`, () => {
    const selected = []
    const { html, tree } = renderWorkspace({ ...workspaceProps(mode, 'error'), onSelectRun: id => selected.push(id) })
    assert.match(html, /aria-busy="false"/)
    assert.match(html, /role="alert"/)
    assert.match(html, /The saved case request timed out\./)
    assert.match(html, new RegExp(backLabel))
    assert.deepEqual(selected, [], 'An error render waits for the user to request a retry')
    const retry = findButton(tree, 'Try again')
    assert.ok(retry, 'The error screen offers retry')
    retry.props.onClick()
    assert.deepEqual(selected, ['run-meta'])
  })

  test(`${mode}: loaded saved details replace the pending screen`, () => {
    const props = workspaceProps(mode, 'loading')
    const selectedRun = { id: 'run-meta', ticker: 'META', question: 'Saved META research', status: 'completed', tasks: [], outputs: [] }
    const { html } = renderWorkspace({
      ...props,
      selectedRun,
      watchlistItems: [{ id: 'watch-meta', run_id: 'run-meta', title: 'Saved META research' }],
    })
    assert.match(html, /<article class="decision-detail-page"/)
    assert.match(html, /Saved META research/)
    assert.match(html, new RegExp(backLabel))
    assert.doesNotMatch(html, /Opening saved research|This case could not open/)
  })
}

test('an error from an older card cannot replace the currently selected request', () => {
  const props = workspaceProps('research', 'error')
  const { html } = renderWorkspace({ ...props, detailLoad: { ...props.detailLoad, runId: 'run-cost' } })
  assert.doesNotMatch(html, /This case could not open|The saved case request timed out/)
})
