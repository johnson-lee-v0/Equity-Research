import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'
const source = await readFile(new URL('../src/navigationModel.ts', import.meta.url),'utf8')
const js = ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText
const {navigationFor} = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
test('old question and result bookmarks share the research view', () => {
  for(const path of ['#questions','#results','#decisions','#tasks','#research','']) assert.equal(navigationFor(path).primary,'research')
})
test('old documents and library bookmarks preserve their reading content inside research',()=> {
  assert.equal(navigationFor('#documents').research,'earnings')
  assert.equal(navigationFor('#research/earnings').research,'earnings')
  assert.equal(navigationFor('#library').research,'history')
  assert.equal(navigationFor('#research/history').research,'history')
})
test('Reddit becomes a research origin filter and scenarios become strategy testing',()=> {
  assert.equal(navigationFor('#reddit').origin,'reddit')
  assert.equal(navigationFor('#reddit').primary,'research')
  assert.equal(navigationFor('#scenarios').primary,'strategies')
  assert.equal(navigationFor('#evidence').primary,'research')
})
test('primary sections preserve the correct data workspace',()=> {
  assert.equal(navigationFor('#watchlist').workspace,'decisions')
  assert.equal(navigationFor('#portfolio').workspace,'portfolio')
  assert.equal(navigationFor('#congress').primary,'congress')
  assert.equal(navigationFor('#strategies').primary,'strategies')
})

test('Memory is its own section and stays in the memory data workspace', () => {
  assert.equal(navigationFor('#memory').primary, 'memory')
  assert.equal(navigationFor('#memory').workspace, 'memory')
  assert.equal(navigationFor('#memory').research, 'cases')
})
