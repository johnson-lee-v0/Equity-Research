import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/runDetailRequest.ts', import.meta.url), 'utf8')
const javascript = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const { createRunDetailRequest } = await import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`)

function abortableRead(signal) {
  return new Promise((resolve, reject) => {
    if (signal.aborted) reject(signal.reason)
    else signal.addEventListener('abort', () => reject(signal.reason), { once: true })
  })
}

test('a card click and background refresh share the same pending detail read', async () => {
  const request = createRunDetailRequest()
  let resolveDetail
  let reads = 0
  const fetchDetail = () => { reads++; return new Promise(resolve => { resolveDetail = resolve }) }
  const click = request.load('run-meta', 'real', fetchDetail)
  const refresh = request.load('run-meta', 'real', fetchDetail)
  assert.equal(click, refresh)
  await Promise.resolve()
  assert.equal(reads, 1)
  resolveDetail({ id: 'run-meta', answers: ['Saved answer'] })
  assert.deepEqual(await click, { id: 'run-meta', answers: ['Saved answer'] })
  assert.deepEqual(await refresh, await click)
  await request.load('run-meta', 'real', async () => { reads++; return { id: 'run-meta', revision: 2 } })
  assert.equal(reads, 2, 'completed results are refreshed rather than cached indefinitely')
})

test('switching cards cancels the previous request without clearing the new one', async () => {
  const request = createRunDetailRequest()
  const first = request.load('run-meta', 'real', abortableRead)
  const rejected = assert.rejects(first, { name: 'AbortError' })
  await Promise.resolve()
  let finish
  const second = request.load('run-cost', 'real', () => new Promise(resolve => { finish = resolve }))
  await rejected
  assert.equal(request.load('run-cost', 'real', () => { throw new Error('Duplicate fetch') }), second)
  finish({ id: 'run-cost' })
  assert.deepEqual(await second, { id: 'run-cost' })
})

test('the same run id in another namespace is a separate read', async () => {
  const request = createRunDetailRequest()
  const real = request.load('run-same', 'real', abortableRead)
  const rejected = assert.rejects(real, { name: 'AbortError' })
  await Promise.resolve()
  const demo = request.load('run-same', 'demo', async () => ({ namespace: 'demo' }))
  await rejected
  assert.deepEqual(await demo, { namespace: 'demo' })
})

test('failed detail reads can be retried without retaining a rejected promise', async () => {
  const request = createRunDetailRequest()
  await assert.rejects(request.load('run-meta', 'real', async () => { throw new Error('Connection interrupted') }), /Connection interrupted/)
  assert.deepEqual(await request.load('run-meta', 'real', async () => ({ id: 'run-meta' })), { id: 'run-meta' })
})

test('returning to the list cancels the detail request', async () => {
  const request = createRunDetailRequest()
  const pending = request.load('run-meta', 'real', abortableRead)
  const rejected = assert.rejects(pending, { name: 'AbortError' })
  request.cancel()
  await rejected
})

test('a stalled network request becomes a retryable timeout', async () => {
  const request = createRunDetailRequest(10)
  await assert.rejects(request.load('run-meta', 'real', abortableRead), /Opening this case took too long/)
  assert.equal(await request.load('run-meta', 'real', async () => 'Loaded'), 'Loaded')
})
