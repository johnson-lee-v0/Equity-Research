import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const require = createRequire(import.meta.url)
const { build } = createRequire(require.resolve('vite'))('esbuild')
const bundle = await build({
  stdin: {
    contents: "export {default as EarningsBrief} from './EarningsBrief'; export {default as EarningsTrends, CapexGuidance} from './EarningsTrends'",
    resolveDir: fileURLToPath(new URL('../src/panels/research', import.meta.url)),
  },
  bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external', jsx: 'automatic',
  loader: { '.css': 'empty' }, logLevel: 'silent',
})
const compiled = { exports: {} }
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(require, compiled, compiled.exports)
const { EarningsBrief, EarningsTrends, CapexGuidance } = compiled.exports
const render = (component, props) => renderToStaticMarkup(React.createElement(component, props))

function capexSeries(id, actuals) {
  return {
    id, label: id === 'capex' ? 'Management capex' : 'Cash purchases of PP&E',
    unit: 'USD billions', frequency: 'annual', area_ids: ['capital'],
    basis: id === 'capex' ? 'Management stated capex definition' : 'Cash purchases of property, plant and equipment',
    points: actuals.map((value, index) => ({
      period: `FY${2021 + index}`, kind: 'actual', value,
      source_id: `source-${index}`, url: `https://investor.example.com/annual-${2021 + index}`, title: `Annual report ${2021 + index}`,
    })),
  }
}

test('the brief visibly selects available cash history and labels missing management history separately', () => {
  const management = capexSeries('capex', [null, null, null, null, null])
  management.points.push({ period: 'FY2026', kind: 'guidance', value: 8 })
  const cash = capexSeries('capex_cash_ppe', [1, 2, 3, 4, 5])
  const html = render(EarningsBrief, {
    ticker: 'META', result: { sentences: [], themes: [] }, onRead: () => {},
    trends: { series: [management, cash], status: 'partial', version: 'test', capex_guidance: { comparisons: [] } },
  })
  assert.match(html, /aria-pressed="false">Management capex<small[^>]*>0\/5 reported years · 1 guidance year/)
  assert.match(html, /aria-pressed="true">Cash purchases of PP&amp;E<small[^>]*>5\/5 reported years/)
  assert.match(html, /Cash purchases of PP&amp;E over time/)
  assert.match(html, /Cash PP&amp;E alone is not a substitute for management capex/)
  assert.match(html, /aria-label="Cash purchases of PP&amp;E, FY2025, Reported actual: \$5bn — Annual report 2025"/)
  assert.match(html, /Cash PP&amp;E is shown separately and is not substituted/)
})

test('a guidance-only measure explicitly says its historical actuals are missing', () => {
  const series = capexSeries('capex', [null, null, null, null, null])
  series.points.push({ period: 'FY2026', kind: 'guidance', value: 8 })
  const html = render(EarningsTrends, { series, choices: [series], onSelect: () => {} })
  assert.match(html, /0\/5 reported years · 1 guidance year/)
  assert.match(html, /Only guidance is available for this measure; historical actuals are still missing/)
  assert.match(html, /Dashed bars show guidance, not reported results/)
})

test('a calculated management actual exposes both cash operands and the issuer definition with descriptive sources', () => {
  const series = capexSeries('capex', [null])
  series.points = [{
    period: 'FY2025', kind: 'actual', value: 30, measure_basis: 'cash_ppe_plus_finance_lease_principal',
    source_id: 'cash', url: 'https://investor.example.com/cash', title: 'Cash flow report',
    quote: 'Original cash fact is 28, not the combined total.',
    calculation: {
      formula: '(cash purchases of PP&E + finance-lease principal payments) / 1,000,000,000',
      basis: 'Issuer-defined capex including finance-lease principal',
      inputs: [
        { tag: 'PaymentsToAcquirePropertyPlantAndEquipment', value: '28000000000', unit: 'USD', source_id: 'cash', url: 'https://investor.example.com/cash', quote: 'Cash purchases 28 billion' },
        { tag: 'FinanceLeasePrincipalPayments', value: '2000000000', unit: 'USD', source_id: 'lease', url: 'https://investor.example.com/lease', quote: 'Principal payments 2 billion' },
      ],
    },
    definition_source: { url: 'https://investor.example.com/definition', quote: 'Our capital expenditure includes finance lease principal payments.' },
  }]
  const html = render(EarningsTrends, { series, choices: [series], onSelect: () => {} })
  assert.match(html, /Calculated actual/)
  assert.match(html, /Calculated actuals add cash PP&amp;E purchases and finance lease principal from the same filing/)
  assert.match(html, /Cash purchases of PP&amp;E: \$28bn/)
  assert.match(html, /Finance lease principal payments: \$2bn/)
  assert.match(html, /aria-label="FY2025, Finance lease principal payments: \$2bn — source"/)
  assert.match(html, /Company’s capex definition/)
  assert.match(html, /Our capital expenditure includes finance lease principal payments/)
  assert.match(html, /Cash purchases 28 billion/)
  assert.match(html, /Principal payments 2 billion/)
  const comparisonHtml = render(CapexGuidance, { guidance: { comparisons: [{
    period: 'FY2025', initial_low: 30, initial_high: 32, actual: 30, actual_source: series.points[0],
  }] } })
  assert.match(comparisonHtml, /How this actual was calculated/)
  assert.match(comparisonHtml, /Finance lease principal payments: \$2bn/)
  assert.match(comparisonHtml, /Our capital expenditure includes finance lease principal payments/)
})

test('guidance citation labels retain approximate qualifiers and do not hide the management basis', () => {
  const html = render(CapexGuidance, { guidance: { comparisons: [{
    period: 'FY2025', initial_low: 30, initial_high: 30, initial_qualifier: 'approximately', actual: 31,
    initial_source: { url: 'https://investor.example.com/guidance', title: 'Earnings call' },
  }] } })
  assert.match(html, /aria-label="FY2025, earliest captured management capex guidance: Approximately \$30bn — Earnings call"/)
  assert.match(html, /Management capex only, using actuals on the same basis as guidance/)
})
