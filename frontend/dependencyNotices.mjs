import { readFileSync, readdirSync, realpathSync, statSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { createRequire } from 'node:module'
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path'

const legalName = /^(?:licen[cs]es?|copying|notices?|copyright)(?:[._ -].*)?$/i
const licenseName = /^(?:licen[cs]es?|copying)(?:[._ -].*)?$/i
// These runtime helpers are supplied by Vite. Its published LICENSE.md also
// contains the complete notices for its bundled @rollup/plugin-commonjs.
const viteHelpers = new Set([
  '\0vite/modulepreload-polyfill.js',
  '\0vite/preload-helper.js',
  '\0commonjsHelpers.js',
])
// This exact npm release omits its upstream LICENSE file. Do not infer license
// text from an SPDX identifier or reuse this exception for another version.
const supplementalLicenses = {
  '@react-three/fiber@9.3.0': {
    file: 'react-three-fiber-9.3.0.LICENSE',
    license: 'MIT',
    source: 'https://raw.githubusercontent.com/pmndrs/react-three-fiber/e53d667aa0326024be2772558a7a593e2bb825d3/LICENSE',
    sha256: '9c35b5de7b7493a707fffe4eb23bd2f7f449153c1911f7c6eefb4e591fd5349a',
  },
}

function inside(root, file) {
  const path = relative(root, file)
  return path === '' || (!path.startsWith(`..${sep}`) && path !== '..' && !isAbsolute(path))
}

function packageRoot(file) {
  // Use the nearest node_modules boundary, including scoped and pnpm packages.
  const marker = `${sep}node_modules${sep}`
  const at = file.lastIndexOf(marker)
  if (at !== -1) {
    const parts = file.slice(at + marker.length).split(sep)
    return file.slice(0, at + marker.length) + parts.slice(0, parts[0].startsWith('@') ? 2 : 1).join(sep)
  }
  // Also handle linked dependencies whose real path is outside the app root.
  for (let directory = dirname(file); ; directory = dirname(directory)) {
    try {
      const pkg = JSON.parse(readFileSync(join(directory, 'package.json'), 'utf8'))
      if (pkg.name && pkg.version) return directory
    } catch (error) {
      if (error.code !== 'ENOENT') throw error
    }
    if (directory === dirname(directory)) break
  }
  throw new Error(`Cannot identify the package for bundled module: ${file}`)
}

function packageNotices(root, appRoot) {
  const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'))
  if (!pkg.name || !pkg.version) throw new Error(`Missing package name/version: ${root}`)
  const files = new Set()
  function walk(directory, legalDirectory = false) {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      if (entry.name === 'node_modules' || entry.name === '.git') continue
      const file = join(directory, entry.name)
      if (entry.isDirectory()) walk(file, legalDirectory || legalName.test(entry.name))
      else if ((legalDirectory || legalName.test(entry.name)) && (entry.isFile() || entry.isSymbolicLink())) files.add(file)
    }
  }
  walk(root)
  // npm permits a custom filename through "SEE LICENSE IN <filename>".
  const custom = typeof pkg.license === 'string' && /^SEE LICENSE IN (.+)$/i.exec(pkg.license)
  if (custom) files.add(resolve(root, custom[1]))
  const supplemental = supplementalLicenses[`${pkg.name}@${pkg.version}`]
  if (![...files].some((file) => relative(root, file).split(sep).some((part) => licenseName.test(part))) && !custom && !supplemental) {
    throw new Error(`No full LICENSE or COPYING file found for ${pkg.name}@${pkg.version}; review the package before publishing.`)
  }
  const documents = [...files].sort().map((file) => {
    if (!inside(root, realpathSync(file)) || !statSync(file).isFile()) {
      throw new Error(`License file escapes its package or is not a file: ${file}`)
    }
    const text = new TextDecoder('utf-8', { fatal: true }).decode(readFileSync(file))
    if (!text.trim() || text.includes('\0')) throw new Error(`Empty or binary license/notice file: ${file}`)
    return { file: relative(root, file).split(sep).join('/'), text }
  })
  if (supplemental) {
    const bytes = readFileSync(join(appRoot, 'third-party-licenses', supplemental.file))
    if (pkg.license !== supplemental.license || createHash('sha256').update(bytes).digest('hex') !== supplemental.sha256) {
      throw new Error(`Pinned upstream license verification failed for ${pkg.name}@${pkg.version}`)
    }
    documents.push({ file: `Upstream LICENSE: ${supplemental.source} (SHA-256 ${supplemental.sha256})`, text: bytes.toString('utf8') })
  }
  return { name: pkg.name, version: pkg.version, license: pkg.license ?? pkg.licenses ?? 'See license texts below', documents }
}

/** Build notices from the final chunks, including dynamic chunks and CSS modules. */
export function collectDependencyNotices(bundle, { root, vitePackageRoot }) {
  const appRoot = realpathSync(root)
  const roots = new Set()
  function include(id, renderedLength = 1) {
    if (viteHelpers.has(id)) {
      roots.add(realpathSync(vitePackageRoot))
      return
    }
    const file = id.replace(/^\0/, '').split('?')[0]
    if (!isAbsolute(file)) {
      if (renderedLength > 0) throw new Error(`Unattributed bundled module: ${id}; add its upstream license mapping before publishing.`)
      return
    }
    const actual = realpathSync(file)
    if (!actual.includes(`${sep}node_modules${sep}`) && inside(appRoot, actual)) return
    roots.add(realpathSync(packageRoot(actual)))
  }
  for (const output of Object.values(bundle)) {
    if (output.type === 'chunk') {
      for (const external of [...output.imports, ...output.dynamicImports]) {
        if (!bundle[external]) throw new Error(`External runtime import requires a license review: ${external}`)
      }
      for (const [id, module] of Object.entries(output.modules)) include(id, module.renderedLength)
    } else {
      // Package assets can be emitted without a rendered JavaScript module.
      for (const file of output.originalFileNames ?? []) include(resolve(appRoot, file))
    }
  }
  return [...roots].map((packageDirectory) => packageNotices(packageDirectory, appRoot)).sort((a, b) => `${a.name}@${a.version}`.localeCompare(`${b.name}@${b.version}`))
}

export function formatDependencyNotices(packages) {
  const lines = [
    'THIRD-PARTY SOFTWARE LICENSES AND NOTICES',
    '',
    'Generated from modules and assets in the shipped Vite/Rollup build.',
    'Each package retains its own license. The repository MIT license does not replace these terms.',
    'Complete license and notice files from the installed packages follow, including any upstream bundled notices.',
    '',
    'Package inventory:',
    ...packages.map((pkg) => `- ${pkg.name}@${pkg.version}`),
  ]
  for (const pkg of packages) {
    lines.push('', '='.repeat(78), `${pkg.name}@${pkg.version}`, `Declared license: ${typeof pkg.license === 'string' ? pkg.license : JSON.stringify(pkg.license)}`)
    for (const document of pkg.documents) lines.push('', `--- ${document.file} ---`, '', document.text)
  }
  return `${lines.join('\n')}\n`
}

/** Install only for public-demo builds; no source notices are manually copied. */
export function dependencyNotices() {
  let root
  let vitePackageRoot
  return {
    name: 'public-demo-dependency-notices',
    apply: 'build',
    enforce: 'post',
    configResolved(config) {
      root = config.root
      const require = createRequire(join(root, 'package.json'))
      vitePackageRoot = dirname(require.resolve('vite/package.json'))
    },
    generateBundle(_options, bundle) {
      const packages = collectDependencyNotices(bundle, { root, vitePackageRoot })
      this.emitFile({ type: 'asset', fileName: 'THIRD_PARTY_NOTICES.txt', source: formatDependencyNotices(packages) })
      this.emitFile({ type: 'asset', fileName: 'LICENSE.txt', source: readFileSync(resolve(root, '../LICENSE')) })
    },
  }
}
