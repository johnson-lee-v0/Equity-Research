import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const require = createRequire(import.meta.url)
const { build } = createRequire(require.resolve('vite'))('esbuild')
const bundle = await build({
  entryPoints: [fileURLToPath(new URL('../src/components/MarketNews.tsx', import.meta.url))],
  bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external', jsx: 'automatic',
  loader: { '.css': 'empty' }, logLevel: 'silent',
})
const compiled = { exports: {} }
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(require, compiled, compiled.exports)
const { MarketNewsShelf } = compiled.exports
const items = Array.from({ length: 8 }, (_, i) => ({ id: `news-${i}`, title: `Published market headline ${i}`, url: `https://www.marketwatch.com/story/${i}`, source: 'MarketWatch', published_at: '2026-09-26T15:00:00Z' }))
const checkedAt = new Date().toISOString()
const render = props => renderToStaticMarkup(React.createElement(MarketNewsShelf, props))

test('six linked tiles preserve provider headline, attribution, date and original article', () => {
  const html = render({ snapshot: { status: 'fresh', items, fetched_at: checkedAt } })
  assert.equal((html.match(/class="market-news-tile"/g) ?? []).length, 6)
  assert.match(html, /Published market headline 0/)
  assert.doesNotMatch(html, /Published market headline 6/)
  assert.match(html, /MarketWatch/)
  assert.match(html, /dateTime="2026-09-26T15:00:00Z"/i)
  assert.match(html, /href="https:\/\/www.marketwatch.com\/story\/0"/)
  assert.equal((html.match(/rel="noopener noreferrer"/g) ?? []).length, 6)
})

test('stale and unavailable states are explicit, with no manufactured filler tiles', () => {
  const stale = render({ snapshot: { status: 'stale', items: items.slice(0, 2), fetched_at: checkedAt } })
  assert.match(stale, /Saved headlines/)
  assert.match(stale, /last saved headlines/)
  assert.equal((stale.match(/class="market-news-tile"/g) ?? []).length, 2)
  const unavailable = render({ snapshot: { status: 'unavailable', items: [], fetched_at: null }, onRetry() {} })
  assert.match(unavailable, /temporarily unavailable/)
  assert.match(unavailable, /Try again/)
  assert.doesNotMatch(unavailable, /class="market-news-tile"/)
})

test('unsafe URLs are omitted and feed text is never treated as HTML', () => {
  const html = render({ snapshot: { status: 'fresh', items: [{ ...items[0], title: '<script>alert(1)</script>' }, { ...items[1], url: 'javascript:alert(1)' }], fetched_at: null } })
  assert.equal((html.match(/class="market-news-tile"/g) ?? []).length, 1)
  assert.doesNotMatch(html, /<script>|javascript:/)
  assert.match(html, /&lt;script&gt;/)
})

test('a static snapshot cannot keep claiming latest headlines indefinitely', () => {
  const html = render({ snapshot: { status: 'fresh', items, fetched_at: '2020-01-01T00:00:00Z' } })
  assert.match(html, /Saved headlines/)
  assert.match(html, /temporarily unavailable/)
  assert.doesNotMatch(html, /class="market-news-tile"|Latest headlines/)
})
