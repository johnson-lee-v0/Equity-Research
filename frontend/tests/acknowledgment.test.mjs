import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import React from 'react'

const require = createRequire(import.meta.url)
const { build } = createRequire(require.resolve('vite'))('esbuild')
const sourcePath = fileURLToPath(new URL('../src/demo/AcknowledgedDemo.tsx', import.meta.url))
const bundle = await build({
  entryPoints: [sourcePath], bundle: true, write: false, platform: 'node', format: 'cjs',
  packages: 'external', jsx: 'automatic', loader: { '.css': 'empty' }, logLevel: 'silent',
  supported: { 'dynamic-import': false },
  plugins: [{ name: 'isolate-meta-entry', setup(builder) {
    builder.onResolve({ filter: /^\.\/DemoApp$/ }, () => ({ path: 'ack-meta-test-double', external: true }))
  } }],
})

// Exercise the component's real state, handlers and effects without adding a DOM
// dependency. The native dialog's close/cancel order and ref focus are modeled;
// actual keyboard focus containment remains a browser-level verification.
function createVisit() {
  const slots = []
  let cursor = 0
  let effects = []
  let changed = false
  let nodes = []
  let activeElement = null
  let demoImports = 0
  let demoMounts = 0
  const domByRef = new Map()
  const runtime = {
    ...React,
    useState(initial) {
      const index = cursor++
      if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial
      return [slots[index], update => {
        const value = typeof update === 'function' ? update(slots[index]) : update
        if (!Object.is(value, slots[index])) { slots[index] = value; changed = true }
      }]
    },
    useRef(initial) {
      const index = cursor++
      return slots[index] ??= { current: initial }
    },
    useEffect(effect, dependencies) {
      const index = cursor++
      const previous = slots[index]
      if (!previous || !dependencies || dependencies.some((value, i) => !Object.is(value, previous.dependencies?.[i]))) {
        effects.push(() => {
          previous?.cleanup?.()
          slots[index] = { dependencies, cleanup: effect() }
        })
      }
    },
    lazy(load) { return { acknowledgmentTestLazy: true, load, loaded: null } },
    Suspense({ children }) { return children },
  }
  const forbidden = new Proxy({}, { get() { throw new Error('Acknowledgment must not access persistent storage') } })
  const module = { exports: {} }
  const moduleRequire = name => {
    if (name === 'react') return runtime
    if (name === 'ack-meta-test-double') {
      demoImports++
      return function MetaDemoDouble() {
        demoMounts++
        return React.createElement('section', { 'data-test-meta-demo': true }, 'Original META research')
      }
    }
    return require(name)
  }
  new Function('require', 'module', 'exports', 'localStorage', 'sessionStorage', 'window', 'document', 'fetch', bundle.outputFiles[0].text)(
    moduleRequire, module, module.exports, forbidden, forbidden,
    { localStorage: forbidden, sessionStorage: forbidden }, forbidden,
    () => { throw new Error('Acknowledgment must not send a receipt') },
  )

  async function visitTree(element) {
    if (element == null || typeof element === 'boolean' || typeof element === 'string' || typeof element === 'number') return
    if (Array.isArray(element)) { for (const child of element) await visitTree(child); return }
    if (element.type?.acknowledgmentTestLazy) {
      const lazy = element.type
      lazy.loaded ??= await lazy.load()
      await visitTree(lazy.loaded.default(element.props))
      return
    }
    if (typeof element.type === 'function') {
      await visitTree(element.type.prototype?.render ? new element.type(element.props).render() : element.type(element.props))
      return
    }
    if (typeof element.type === 'string') {
      const { ref } = element.props
      const identity = `${element.type}|${element.props.className ?? ''}|${element.props.id ?? ''}`
      const previous = ref && domByRef.get(ref)
      // The overview and accepted banner are different DOM branches, even
      // though they assign their differently styled opener buttons to one ref.
      const node = (previous?.identity === identity && previous) || {
        type: element.type, identity, props: element.props, open: false,
        focus() { activeElement = this },
        showModal() { this.open = true },
        close() {
          if (!this.open) return
          this.open = false
          this.props.onClose?.({ target: this })
        },
      }
      node.props = element.props
      if (ref) { domByRef.set(ref, node); ref.current = node }
      nodes.push(node)
    }
    await visitTree(element.props?.children)
  }
  async function render() {
    for (let iteration = 0; iteration < 10; iteration++) {
      changed = false
      cursor = 0
      effects = []
      nodes = []
      await visitTree(module.exports.default())
      for (const effect of effects) effect()
      if (!changed) return
    }
    throw new Error('Acknowledgment effects did not settle')
  }
  const find = predicate => {
    const node = nodes.find(predicate)
    assert.ok(node, 'Expected element was rendered')
    return node
  }
  const byType = type => find(node => node.type === type)
  const submitButton = () => find(node => node.type === 'button' && node.props.type === 'submit')
  const opener = () => find(node => node.type === 'button' && node.props['aria-haspopup'] === 'dialog')
  return {
    render, byType, submitButton, opener,
    Boundary: module.exports.ResearchLoadBoundary,
    get activeElement() { return activeElement },
    get demoImports() { return demoImports },
    get demoMounts() { return demoMounts },
    get hasMeta() { return nodes.some(node => node.props['data-test-meta-demo']) },
    async check(value) { byType('input').props.onChange({ target: { checked: value } }); await render() },
    async submit() {
      let prevented = false
      byType('form').props.onSubmit({ preventDefault() { prevented = true } })
      assert.equal(prevented, true, 'Submission must not navigate or transmit a form')
      await render()
    },
    async dismiss() {
      find(node => node.type === 'button' && node.props.type === 'button' && !node.props['aria-haspopup'] && node.props.className !== 'research-ack-top-close').props.onClick()
      await render()
    },
    topClose() { return find(node => node.props.className === 'research-ack-top-close') },
    async dismissTop() {
      find(node => node.props.className === 'research-ack-top-close').props.onClick()
      await render()
    },
    async escape() {
      const dialog = byType('dialog')
      let prevented = false
      dialog.props.onCancel?.({ preventDefault() { prevented = true } })
      if (!prevented) dialog.close()
      await render()
    },
    async reopen() { opener().props.onClick(); await render() },
  }
}

test('a failed research chunk shows a recoverable message instead of clearing the page', () => {
  const { Boundary } = createVisit()
  const content = React.createElement('section', null, 'META research')
  const boundary = new Boundary({ children: content })
  assert.equal(boundary.render(), content)
  boundary.state = Boundary.getDerivedStateFromError(new Error('Unavailable chunk'))
  const fallback = boundary.render()
  assert.equal(fallback.props.role, 'alert')
  const children = React.Children.toArray(fallback.props.children)
  assert.equal(children[0].props.children, 'The research example could not load.')
  assert.match(children[1].props.children, /acknowledge the notice again/)
  assert.equal(children[2].props.children, 'Reload the example')
  assert.equal(typeof children[2].props.onClick, 'function')
})

test('first visit opens a labeled notice and does not import or mount META', async () => {
  const visit = createVisit()
  await visit.render()
  assert.equal(visit.hasMeta, false)
  assert.equal(visit.demoImports, 0)
  assert.equal(visit.demoMounts, 0)
  assert.equal(visit.byType('h1').props.children, 'Research, from question to reviewed decision.')
  assert.equal(visit.opener().props.children, 'Review notice & explore the research workflow')
  assert.equal(visit.byType('dialog').open, true)
  assert.equal(visit.byType('dialog').props['aria-labelledby'], 'research-ack-title')
  assert.equal(visit.byType('dialog').props['aria-describedby'], 'research-ack-description')
  assert.equal(visit.activeElement.props.id, 'research-ack-title')
  assert.equal(visit.byType('input').props.checked, false)
  assert.equal(visit.byType('input').props.required, true)
  assert.equal(visit.submitButton().props.disabled, true)
})

test('unchecked submit is guarded even if invoked directly', async () => {
  const visit = createVisit()
  await visit.render()
  await visit.submit()
  assert.equal(visit.hasMeta, false)
  assert.equal(visit.demoImports, 0)
  assert.equal(visit.byType('dialog').open, true)
  await visit.check(true)
  assert.equal(visit.submitButton().props.disabled, false)
  await visit.check(false)
  assert.equal(visit.submitButton().props.disabled, true)
})

test('canceling never unlocks META and reopening clears a previously checked box', async () => {
  const visit = createVisit()
  await visit.render()
  await visit.check(true)
  await visit.dismiss()
  assert.equal(visit.byType('dialog').open, false)
  assert.equal(visit.activeElement, visit.opener())
  assert.equal(visit.hasMeta, false)
  assert.equal(visit.demoImports, 0)
  await visit.reopen()
  assert.equal(visit.byType('dialog').open, true)
  assert.equal(visit.byType('input').props.checked, false)
  assert.equal(visit.submitButton().props.disabled, true)
})

test('Escape resets acknowledgment and closes to the overview without unlocking', async () => {
  const visit = createVisit()
  await visit.render()
  await visit.check(true)
  await visit.escape()
  assert.equal(visit.byType('dialog').open, false)
  assert.equal(visit.byType('input').props.checked, false)
  assert.equal(visit.activeElement, visit.opener())
  assert.equal(visit.hasMeta, false)
  assert.equal(visit.demoImports, 0)
})

test('top close is a named non-submit control that never grants acceptance', async () => {
  const visit = createVisit()
  await visit.render()
  assert.equal(visit.topClose().props.type, 'button')
  assert.equal(visit.topClose().props['aria-label'], 'Close notice and return to overview')
  await visit.dismissTop()
  assert.equal(visit.byType('dialog').open, false)
  assert.equal(visit.hasMeta, false)
  assert.equal(visit.demoImports, 0)
  assert.equal(visit.activeElement, visit.opener())
  await visit.reopen()
  await visit.check(true)
  await visit.dismissTop()
  assert.equal(visit.hasMeta, false, 'Even a checked box does not accept when closing')
  await visit.reopen()
  assert.equal(visit.byType('input').props.checked, false)
  await visit.check(true)
  await visit.submit()
  await visit.reopen()
  assert.equal(visit.topClose().props['aria-label'], 'Close notice and return to research')
  await visit.dismissTop()
  assert.equal(visit.hasMeta, true)
  assert.equal(visit.byType('dialog').open, false)
  assert.equal(visit.activeElement, visit.opener())
})

test('checked submission mounts META and later notice dismissal retains that visit acceptance', async () => {
  const visit = createVisit()
  await visit.render()
  await visit.check(true)
  await visit.submit()
  assert.equal(visit.hasMeta, true)
  assert.equal(visit.demoImports, 1)
  assert.ok(visit.demoMounts > 0)
  assert.equal(visit.byType('dialog').open, false)
  assert.equal(visit.activeElement, visit.opener())
  await visit.reopen()
  assert.equal(visit.byType('input').props.checked, false)
  assert.equal(visit.submitButton().props.disabled, true)
  await visit.escape()
  assert.equal(visit.hasMeta, true, 'Re-reading the notice must not destroy an accepted research session')
  assert.equal(visit.byType('dialog').open, false)
})

test('a new page visit requires fresh acknowledgment and uses no persistent receipt', async () => {
  const acceptedVisit = createVisit()
  await acceptedVisit.render()
  await acceptedVisit.check(true)
  await acceptedVisit.submit()
  const nextVisit = createVisit()
  await nextVisit.render()
  assert.equal(nextVisit.hasMeta, false)
  assert.equal(nextVisit.demoImports, 0)
  assert.equal(nextVisit.byType('dialog').open, true)
  assert.equal(nextVisit.submitButton().props.disabled, true)
  const source = readFileSync(sourcePath, 'utf8')
  assert.doesNotMatch(source, /localStorage|sessionStorage|indexedDB|document\.cookie|sendBeacon|fetch\s*\(/)
})
