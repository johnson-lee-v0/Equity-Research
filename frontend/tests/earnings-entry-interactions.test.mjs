import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const require = createRequire(import.meta.url)
const { build } = createRequire(require.resolve('vite'))('esbuild')
const paths = ['DemoApp', 'EarningsCallReview', 'MetaPricing']
const bundles = await Promise.all(paths.map(async name => {
  const result = await build({
    entryPoints: [fileURLToPath(new URL(`../src/demo/${name}.tsx`, import.meta.url))],
    bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external', jsx: 'automatic',
    define: { 'import.meta.env.BASE_URL': '"./"' }, loader: { '.css': 'empty' }, logLevel: 'silent',
  })
  return [name, result.outputFiles[0].text]
}))
const compiled = Object.fromEntries(bundles)

function elements(tree, ancestors = []) {
  if (tree == null || typeof tree === 'boolean' || typeof tree === 'string' || typeof tree === 'number') return []
  if (Array.isArray(tree)) return tree.flatMap(child => elements(child, ancestors))
  return [{ element: tree, ancestors }, ...elements(tree.props?.children, [...ancestors, tree])]
}
function textContent(tree) {
  if (tree == null || typeof tree === 'boolean') return ''
  if (typeof tree === 'string' || typeof tree === 'number') return String(tree)
  if (Array.isArray(tree)) return tree.map(textContent).join('')
  return textContent(tree.props?.children)
}

// Exercise root components with real initializers and handlers. Child components
// use React's normal hooks during SSR; their state never consumes the root slots.
function mount(name, initialHash = '') {
  const slots = []
  const listeners = new Map()
  const frames = []
  const scrolled = []
  let cursor = 0
  let direct = false
  let changed = false
  let effects = []
  let tree
  const location = { hash: initialHash }
  const runtime = {
    ...React,
    useState(initial) {
      if (!direct) return React.useState(initial)
      const index = cursor++
      if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial
      return [slots[index], update => {
        const next = typeof update === 'function' ? update(slots[index]) : update
        if (!Object.is(next, slots[index])) { slots[index] = next; changed = true }
      }]
    },
    useRef(initial) {
      if (!direct) return React.useRef(initial)
      return slots[cursor++] ??= { current: initial }
    },
    useEffect(effect, dependencies) {
      if (!direct) return React.useEffect(effect, dependencies)
      const index = cursor++
      const prior = slots[index]
      if (!prior || !dependencies || dependencies.some((value, i) => !Object.is(value, prior.dependencies?.[i]))) {
        effects.push(() => { prior?.cleanup?.(); slots[index] = { effect: true, dependencies, cleanup: effect() } })
      }
    },
  }
  const module = { exports: {} }
  new Function('require', 'module', 'exports', 'window', 'document', 'requestAnimationFrame', compiled[name])(
    dependency => dependency === 'react' ? runtime : require(dependency), module, module.exports,
    { location, addEventListener: (type, handler) => listeners.set(type, handler), removeEventListener: (type, handler) => { assert.equal(listeners.get(type), handler); listeners.delete(type) } },
    { getElementById: id => renderToStaticMarkup(tree).includes(`id="${id}"`) ? { scrollIntoView: options => scrolled.push({ id, options }) } : null },
    handler => frames.push(handler),
  )
  function render() {
    for (let iteration = 0; iteration < 10; iteration++) {
      changed = false; cursor = 0; effects = []; direct = true
      try { tree = module.exports.default() } finally { direct = false }
      for (const effect of effects) effect()
      if (!changed) { while (frames.length) frames.shift()(); return tree }
    }
    throw new Error('Component effects did not settle')
  }
  const find = predicate => {
    const result = elements(tree).find(({ element, ancestors }) => predicate(element, ancestors))
    assert.ok(result, 'Expected element was rendered')
    return result
  }
  render()
  return {
    render, find, exports: module.exports, location, listeners, scrolled,
    get tree() { return tree },
    html: () => renderToStaticMarkup(tree),
    click(label) { find(element => ['button', 'a'].includes(element.type) && textContent(element).includes(label)).element.props.onClick(); render() },
    hash(value) { location.hash = value; listeners.get('hashchange')?.(); render() },
    unmount() { for (const slot of slots) if (slot?.effect) slot.cleanup?.() },
  }
}

test('the default public walkthrough leads with agent engineering and keeps call analysis before financials and news', () => {
  const visit = mount('DemoApp')
  const html = visit.html()
  const primary = elements(visit.tree).filter(({ element }) => element.type === 'a' && element.props.className === 'demo-primary')
  assert.equal(textContent(primary[0].element), 'Explore the agent workflow →')
  assert.equal(primary[0].element.props.href, '#research')
  const callAction = visit.find(element => element.type === 'a' && textContent(element) === 'Inspect earnings-call analysis →')
  assert.equal(callAction.element.props.href, '#earnings-call')
  assert.match(html, /From research question to reviewed decision\./)
  assert.ok(html.indexOf('End-to-end agent workflow') < html.indexOf('META research walkthrough'))
  assert.match(html, /Earnings-call NLP is one component of the local research workflow/)
  assert.match(html, /META research walkthrough/)
  assert.match(html, /id="earnings-call"/)
  assert.match(html, /Read the original earnings-call transcript/)
  assert.ok(html.indexOf('META earnings call analysis') < html.indexOf('Sales rose. Operating profit fell.'))
  assert.ok(html.indexOf('META earnings call analysis') < html.indexOf('Market news sources'))
  assert.match(html, /This static editorial reading is not a live model run/)
  assert.match(html, /not a live model run or automated sentiment score/)
  visit.unmount()
})

test('the visible agent workflow spans intake through review and memory without claiming the static case ran agents', () => {
  const visit = mount('DemoApp')
  const html = renderToStaticMarkup(React.createElement(visit.exports.AgentWorkflow))
  assert.match(html, /<section id="agent-workflow"[^>]*aria-label="End-to-end agent workflow"/)
  assert.equal((html.match(/<li>/g) ?? []).length, 6)
  for (const role of ['Chief of Staff', 'Source discovery', 'Earnings and fundamental research', 'Code checks', 'The CIO', 'ledger and shared company memory']) assert.ok(html.includes(role), role)
  assert.match(html, /No orders are placed/)
  assert.match(html, /does not start agent jobs or replay a recorded autonomous run/)
  assert.doesNotMatch(html, /<details|hidden|live autonomous research/)
  assert.match(html, /href="https:\/\/github\.com\/johnson-lee-v0\/Equity-Research\/blob\/main\/docs\/research-workflow\.md"/)
  visit.hash('#valuation')
  visit.click('Explore the agent workflow')
  assert.equal(visit.location.hash, 'research')
  assert.equal(visit.scrolled.at(-1).id, 'agent-workflow')
  assert.match(visit.html(), /id="valuation"/, 'Inspecting the workflow does not discard the selected research step')
  visit.click('Inspect earnings-call analysis')
  assert.equal(visit.location.hash, 'earnings-call')
  assert.match(visit.html(), /META earnings call analysis/)
  visit.unmount()
})

test('earnings deep links work on initial mount, hash changes and repeated unchanged-hash CTA', () => {
  for (const route of ['#earnings-call', '#earnings', '#EARNINGS', '#earnings%2Dcall']) {
    const visit = mount('DemoApp', route)
    assert.equal(visit.find(element => element.props?.['aria-current'] === 'step').element.props.children[1], 'Earnings & fundamentals')
    assert.equal(visit.scrolled.at(-1).id, 'earnings-call')
    visit.hash('#valuation')
    assert.match(visit.html(), /id="valuation"/)
    visit.hash('#memory')
    assert.equal(visit.find(element => element.props?.['aria-current'] === 'page').element.props.children, 'Memory')
    visit.hash(route)
    assert.match(visit.html(), /META earnings call analysis/)
    const before = visit.scrolled.length
    visit.location.hash = '#earnings-call'
    visit.click('Inspect earnings-call analysis')
    assert.equal(visit.scrolled.length, before + 1, 'CTA works even when the browser emits no hashchange')
    assert.equal(visit.location.hash, 'earnings-call')
    visit.hash('#%E0%A4%A')
    assert.match(visit.html(), /META earnings call analysis/, 'Malformed hashes leave the current view usable')
    visit.unmount()
    assert.equal(visit.listeners.size, 0)
  }
})

test('all research steps and financials remain directly reachable', () => {
  const visit = mount('DemoApp', '#decision')
  assert.match(visit.html(), /id="decision"/)
  visit.hash('#five-questions')
  assert.match(visit.html(), /Five questions, in plain language/)
  visit.click('Review comparable valuation')
  assert.match(visit.html(), /id="valuation"/)
  visit.hash('#financials')
  assert.match(visit.html(), /META earnings call analysis/)
  assert.equal(visit.scrolled.at(-1).id, 'financials')
  visit.unmount()
})

test('theme and caution controls preserve Q&A, source pages and disclosure-only distinctions', () => {
  const visit = mount('EarningsCallReview')
  const discussions = () => elements(visit.tree).filter(({ element }) => element.type?.name === 'CallDiscussion').map(({ element }) => element.props.item)
  const expected = {
    'Growth & advertising': ['recommendations'], 'Profitability & costs': [], 'AI businesses': ['ai-payoff', 'enterprise-distribution'],
    'Consumer agents': ['consumer-agents'], 'Models & competition': ['lab-advantage', 'model-scale', 'open-models', 'model-independence'],
    'Spending & funding': ['capex-uncertainty', 'funding'], 'Capacity & cash': ['payback-delay', 'capacity-constraints'], 'Outlook & risks': [],
  }
  for (const [theme, ids] of Object.entries(expected)) {
    visit.click(theme)
    assert.deepEqual(discussions().map(item => item.id), ids, theme)
    if (!ids.length) assert.match(visit.html(), /not presented as an analyst question/)
    if (['Consumer agents', 'Models & competition'].includes(theme)) {
      assert.equal(elements(visit.tree).some(({ element }) => element.type?.name === 'EarningsTrends'), false)
    }
  }
  visit.click('Caution and negative language')
  assert.deepEqual(discussions().map(item => item.id), ['capex-uncertainty', 'payback-delay', 'capacity-constraints'])
  for (const item of discussions()) {
    const markup = renderToStaticMarkup(React.createElement(visit.exports.CallDiscussion, { item }))
    assert.ok(markup.includes(`href="${item.questionSourceUrl}"`))
    assert.ok(markup.includes(`href="${item.sourceUrl}"`))
    if (item.quote) assert.ok(markup.includes(`href="${item.quote.sourceUrl}"`))
    assert.match(markup, /Speaker context and original wording/)
    assert.doesNotMatch(markup, /<details[^>]*\bopen(?:[ =]|>)/)
  }
  const payback = discussions().find(item => item.id === 'payback-delay')
  assert.equal(payback.answerPage, 15)
  assert.equal(payback.quote.page, 16)
  visit.click('Review outlook & risks')
  assert.deepEqual(discussions(), [])
  assert.equal(visit.find(element => element.type === 'button' && textContent(element).startsWith('Outlook & risks')).element.props['aria-pressed'], true)
})

test('theme chart selections are local to each theme, not fabricated product metrics', () => {
  const visit = mount('EarningsCallReview')
  const chart = () => visit.find(element => element.type?.name === 'EarningsTrends').element
  chart().props.onSelect('revenue'); visit.render()
  assert.equal(chart().props.series.id, 'revenue')
  visit.click('Spending & funding')
  assert.equal(chart().props.series.id, 'capex_quarterly')
  chart().props.onSelect('operating_cash_flow'); visit.render()
  visit.click('Growth & advertising')
  assert.equal(chart().props.series.id, 'revenue')
  visit.click('Spending & funding')
  assert.equal(chart().props.series.id, 'operating_cash_flow')
})

test('target multiples and actual own-company history lead; growth is a closed optional sensitivity', () => {
  const visit = mount('MetaPricing')
  const target = () => visit.find(element => element.props?.id === 'meta-target-multiple')
  const growth = () => visit.find((element, ancestors) => element.type === 'input' && ancestors.some(parent => parent.props?.className === 'demo-growth-sensitivity'))
  const history = () => visit.find(element => element.type?.name === 'ValuationResearchContextView' && element.props.context.historical_multiples).element
  assert.equal(target().ancestors.some(element => element.type === 'details'), false)
  const growthDisclosure = growth().ancestors.find(element => element.type === 'details')
  assert.match(textContent(growthDisclosure.props.children[0]), /Optional: test growth sensitivity/)
  assert.equal(growthDisclosure.props.open, undefined)
  assert.equal(growth().element.props.type, 'number', 'Growth no longer dominates as a slider')
  assert.deepEqual(Object.keys(history().props.context.historical_multiples), ['P/E'])
  assert.equal(history().props.context.historical_multiples['P/E'].points.length, 5)
  assert.equal(history().props.context.historical_multiples['P/E'].sampling, 'annual')
  const html = visit.html()
  assert.match(html, /no verified peer-company comparables are included/)
  assert.match(html, /Stock Analysis/)
  assert.ok(html.indexOf('Company multiple history') < html.indexOf('Optional: test growth sensitivity'))
  target().element.props.onChange({ target: { value: '30' } }); visit.render()
  assert.match(visit.html(), /\$796\.50/, '26.55 reported EPS × 30')
  assert.match(visit.html(), /\$860\.22/, 'Default 8% growth is secondary but explicit')
  growth().element.props.onChange({ target: { value: '20' } }); visit.render()
  assert.match(visit.html(), /\$796\.50/, 'Growth does not change today’s implied value')
  assert.match(visit.html(), /\$955\.80/, 'Growth changes the 12-month scenario exactly once')
  visit.click('P/S')
  assert.equal(target().element.props.value, 8)
  assert.equal(growth().element.props.value, 8)
  assert.deepEqual(Object.keys(history().props.context.historical_multiples), ['P/S'])
  assert.match(visit.html(), /Trailing revenue/)
  visit.click('P/NAV')
  assert.match(visit.html(), /no sourced equity NAV/)
  assert.equal(elements(visit.tree).some(({ element }) => element.props?.id === 'meta-target-multiple'), false)
})
