import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import * as THREE from 'three'

const require = createRequire(import.meta.url)
const { build } = createRequire(require.resolve('vite'))('esbuild')
const bundle = await build({
  entryPoints: [fileURLToPath(new URL('../src/panels/MemoryGraph3D.tsx', import.meta.url))],
  bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external', jsx: 'automatic', logLevel: 'silent',
})
let effects = [], canvasMounts = 0
const hooks = { ...React, useEffect(effect) { effects.push(effect) } }
const fiber = {
  // R3F places this content inside its canvas even when WebGL succeeds. It is
  // browser fallback content, not a conditional graphics-failure notification.
  Canvas({ fallback }) { canvasMounts++; return React.createElement('canvas', null, fallback) },
}
const compiled = { exports: {} }
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(
  name => name === 'react' ? hooks : name === '@react-three/fiber' ? fiber : name === '@react-three/drei' ? {} : require(name),
  compiled, compiled.exports,
)

function renderWithSupport(available) {
  const previousDocument = globalThis.document, previousWindow = globalThis.window
  globalThis.document = { createElement: () => ({ getContext: () => available ? {} : null }) }
  globalThis.window = { WebGLRenderingContext: function WebGLRenderingContext() {} }
  effects = []; canvasMounts = 0
  let failures = 0
  try {
    const html = renderToStaticMarkup(React.createElement(compiled.exports.default, {
      graph: { nodes: [], edges: [] }, selectedId: null, onSelect: () => {}, cameraAction: { type: 'fit', nonce: 0 },
      onUnavailable: () => failures++,
    }))
    for (const effect of effects) effect()
    return { html, failures, canvasMounts }
  } finally {
    if (previousDocument === undefined) delete globalThis.document
    else globalThis.document = previousDocument
    if (previousWindow === undefined) delete globalThis.window
    else globalThis.window = previousWindow
  }
}

test('mounting normal canvas fallback content must not report a supported 3D map unavailable', () => {
  const result = renderWithSupport(true)
  assert.equal(result.canvasMounts, 1)
  assert.match(result.html, /<canvas>/)
  assert.equal(result.failures, 0)
})

test('a genuinely unavailable graphics context falls back without trying to mount the renderer', () => {
  const result = renderWithSupport(false)
  assert.equal(result.canvasMounts, 0)
  assert.doesNotMatch(result.html, /<canvas>/)
  assert.equal(result.failures, 1)
})

test('a precise visible-node click wins over a nearer overlapping enlarged hit target', () => {
  const dots = new THREE.Object3D(), targets = new THREE.Object3D()
  const nodes = [{ id: 'blue-source', x: 8, y: 0, z: 10 }, { id: 'green-fact', x: 0, y: 0, z: 0 }]
  const camera = new THREE.PerspectiveCamera(46, 1, .1, 1000)
  camera.position.z = 100
  camera.updateMatrixWorld()
  const picked = compiled.exports.pickMemoryNode(nodes, [
    { object: targets, instanceId: 0 }, { object: targets, instanceId: 1 }, { object: dots, instanceId: 1 },
  ], dots, targets, { x: 0, y: 0 }, camera, { width: 400, height: 400 })
  assert.equal(picked.id, 'green-fact')
})

test('a click between visible spheres chooses the closest projected note instead of the frontmost expanded target', () => {
  const dots = new THREE.Object3D(), targets = new THREE.Object3D()
  const nodes = [{ id: 'front-but-off-center', x: 8, y: 0, z: 10 }, { id: 'near-pointer', x: 1, y: 0, z: 0 }]
  const camera = new THREE.PerspectiveCamera(46, 1, .1, 1000)
  camera.position.z = 100
  camera.updateMatrixWorld()
  const picked = compiled.exports.pickMemoryNode(nodes, [
    { object: targets, instanceId: 0 }, { object: targets, instanceId: 1 },
  ], dots, targets, { x: 0, y: 0 }, camera, { width: 400, height: 400 })
  assert.equal(picked.id, 'near-pointer')
})
