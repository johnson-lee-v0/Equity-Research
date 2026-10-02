import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, relative } from 'node:path'
import test from 'node:test'
import { collectDependencyNotices, dependencyNotices, formatDependencyNotices } from '../dependencyNotices.mjs'

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'dependency-notices-'))
  t.after(() => rmSync(root, { recursive: true, force: true }))
  const app = join(root, 'frontend')
  mkdirSync(app)
  function write(file, text) {
    mkdirSync(dirname(file), { recursive: true })
    writeFileSync(file, text)
    return file
  }
  function pkg(name, version, documents, parent = join(app, 'node_modules')) {
    const directory = join(parent, name)
    write(join(directory, 'package.json'), JSON.stringify({ name, version, license: 'MIT' }))
    write(join(directory, 'index.js'), 'export const value = 1;')
    for (const [file, text] of Object.entries(documents)) write(join(directory, file), text)
    return directory
  }
  const vite = pkg('vite', '7.1.3', { 'LICENSE.md': 'Complete Vite and bundled CommonJS notices\n' })
  return { root: app, vitePackageRoot: vite, write, pkg }
}

function chunk(modules, extra = {}) {
  return { type: 'chunk', imports: [], dynamicImports: [], modules: Object.fromEntries(modules.map((id) => [id, { renderedLength: 12 }])), ...extra }
}

test('final chunks include multiple versions, pnpm wrappers, CSS, nested notices and emitted assets', (t) => {
  const f = fixture(t)
  const first = f.pkg('@scope/runtime', '1.0.0', {
    'LICENSE': 'Complete first license\n',
    'NOTICE.txt': 'Required attribution\n',
    'vendor/COPYING.LESSER': 'Complete vendored LGPL terms\n',
    'LICENSES/Extra.txt': 'Additional full license\n',
  }, join(f.root, 'node_modules/.pnpm/runtime@1/node_modules'))
  const second = f.pkg('@scope/runtime', '2.0.0', { 'LICENCE-MIT': 'Complete second license\n' }, join(f.root, 'node_modules/.pnpm/runtime@2/node_modules'))
  const css = f.pkg('style-package', '1.1.0', { COPYING: 'Complete CSS license\n' })
  const asset = f.pkg('asset-package', '1.2.0', { 'LICENSE.txt': 'Complete asset license\n' })
  const untouched = f.pkg('not-shipped', '9.0.0', {})
  assert.ok(untouched)
  const cssFile = f.write(join(css, 'style.css'), 'body { color: blue }')
  const assetFile = f.write(join(asset, 'icon.svg'), '<svg/>')
  const bundle = {
    'entry.js': chunk(['\0vite/modulepreload-polyfill.js', '\0commonjsHelpers.js', `\0${join(first, 'index.js')}?commonjs-es-import`], { dynamicImports: ['lazy.js'] }),
    'lazy.js': chunk([join(second, 'index.js')], { modules: { [join(second, 'index.js')]: { renderedLength: 12 }, [cssFile]: { renderedLength: 0 } } }),
    'icon.svg': { type: 'asset', originalFileNames: [relative(f.root, assetFile)] },
  }
  const packages = collectDependencyNotices(bundle, f)
  assert.deepEqual(packages.map(({ name, version }) => `${name}@${version}`), ['@scope/runtime@1.0.0', '@scope/runtime@2.0.0', 'asset-package@1.2.0', 'style-package@1.1.0', 'vite@7.1.3'])
  assert.deepEqual(packages[0].documents.map(({ file }) => file), ['LICENSE', 'LICENSES/Extra.txt', 'NOTICE.txt', 'vendor/COPYING.LESSER'])
  const output = formatDependencyNotices(packages)
  for (const pkg of packages) for (const doc of pkg.documents) assert.ok(output.includes(doc.text))
  assert.ok(!output.includes(f.root), 'published notices must not expose absolute build paths')
  assert.ok(!output.includes('not-shipped'))
})

test('resolves symlinked packages and custom license filenames without editing their text', (t) => {
  const f = fixture(t)
  const linked = f.pkg('linked', '1.0.0', { 'terms.txt': 'Exact full custom terms\r\n' }, join(f.root, '../linked-packages'))
  f.write(join(linked, 'package.json'), JSON.stringify({ name: 'linked', version: '1.0.0', license: 'SEE LICENSE IN terms.txt' }))
  symlinkSync(linked, join(f.root, 'node_modules/linked'), 'dir')
  const packages = collectDependencyNotices({ 'entry.js': chunk([join(f.root, 'node_modules/linked/index.js')]) }, f)
  assert.equal(packages[0].documents[0].text, 'Exact full custom terms\r\n')
})

test('fails closed on missing or empty licenses, unknown helpers and external runtime imports', (t) => {
  const f = fixture(t)
  const missing = f.pkg('missing', '1.0.0', { NOTICE: 'Attribution without license text' })
  assert.throws(() => collectDependencyNotices({ 'entry.js': chunk([join(missing, 'index.js')]) }, f), /No full LICENSE or COPYING/)
  f.write(join(missing, 'LICENSE'), '')
  assert.throws(() => collectDependencyNotices({ 'entry.js': chunk([join(missing, 'index.js')]) }, f), /Empty or binary/)
  assert.throws(() => collectDependencyNotices({ 'entry.js': chunk(['\0unknown-plugin-runtime']) }, f), /Unattributed bundled module/)
  assert.throws(() => collectDependencyNotices({ 'entry.js': chunk([], { imports: ['https://example.invalid/runtime.js'] }) }, f), /External runtime import/)
})

test('plugin emits the original repository license bytes and generated notices', (t) => {
  const f = fixture(t)
  f.write(join(f.root, 'package.json'), '{"name":"fixture","version":"1.0.0"}')
  const license = Buffer.from('MIT License\r\n\r\nExact repository text.\r\n')
  f.write(join(f.root, '../LICENSE'), license)
  const plugin = dependencyNotices()
  plugin.configResolved({ root: f.root })
  const assets = []
  plugin.generateBundle.call({ emitFile: (asset) => assets.push(asset) }, {}, { 'entry.js': chunk(['\0vite/preload-helper.js']) })
  assert.deepEqual(assets.map(({ fileName }) => fileName), ['THIRD_PARTY_NOTICES.txt', 'LICENSE.txt'])
  assert.deepEqual(assets[1].source, readFileSync(join(f.root, '../LICENSE')))
  assert.match(assets[0].source, /vite@7\.1\.3/)
})

test('the missing upstream license exception requires the exact package version and checksum', (t) => {
  const f = fixture(t)
  const fiber = f.pkg('@react-three/fiber', '9.3.0', {})
  const bytes = readFileSync(new URL('../third-party-licenses/react-three-fiber-9.3.0.LICENSE', import.meta.url))
  const fallback = f.write(join(f.root, 'third-party-licenses/react-three-fiber-9.3.0.LICENSE'), bytes)
  const bundle = { 'entry.js': chunk([join(fiber, 'index.js')]) }
  const packages = collectDependencyNotices(bundle, f)
  assert.equal(packages[0].documents[0].text, bytes.toString('utf8'))
  assert.match(packages[0].documents[0].file, /e53d667aa0326024be2772558a7a593e2bb825d3/)
  f.write(fallback, 'Changed license text')
  assert.throws(() => collectDependencyNotices(bundle, f), /Pinned upstream license verification failed/)
  f.write(join(fiber, 'package.json'), JSON.stringify({ name: '@react-three/fiber', version: '9.4.0', license: 'MIT' }))
  assert.throws(() => collectDependencyNotices(bundle, f), /No full LICENSE or COPYING/)
})
