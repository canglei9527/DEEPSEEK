#!/usr/bin/env node
/**
 * Install (or refresh) dsh-router into the local ZCode client.
 *
 * Copies this directory into the local marketplace and plugin cache, then
 * registers the marketplace, the plugin, and its enabled state in the
 * client's state files. Idempotent: re-running refreshes the installed copy
 * and leaves every unrelated plugin untouched.
 *
 *   node scripts/install.mjs
 *
 * Plugin hooks are snapshotted when a session starts, so open a NEW session
 * after installing or refreshing.
 */

import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync, copyFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { homedir } from 'node:os'

const HERE = dirname(fileURLToPath(import.meta.url))
const SRC = join(HERE, '..')

const MP_ID = 'local-dsh-router'
const PLUGIN = 'dsh-router'
const CLI = join(homedir(), '.zcode', 'cli')

const manifest = JSON.parse(readFileSync(join(SRC, '.zcode-plugin', 'plugin.json'), 'utf8'))
const VERSION = manifest.version

const MP_DIR = join(CLI, 'plugins', 'marketplaces', MP_ID)
const CACHE_DIR = join(CLI, 'plugins', 'cache', MP_ID, PLUGIN, VERSION)

function backup(file) {
  const p = join(CLI, file)
  if (!existsSync(p)) return
  copyFileSync(p, `${p}.bak-dsh-router`)
}

function editJson(file, fn) {
  const p = join(CLI, file)
  backup(file)
  const data = JSON.parse(readFileSync(p, 'utf8'))
  const before = JSON.stringify(data)
  fn(data)
  if (JSON.stringify(data) === before) {
    console.log(`  ${file}: already registered`)
    return
  }
  writeFileSync(p, JSON.stringify(data, null, 2) + '\n', 'utf8')
  console.log(`  ${file}: updated`)
}

function copyInto(target) {
  rmSync(target, { recursive: true, force: true })
  mkdirSync(target, { recursive: true })
  cpSync(SRC, target, {
    recursive: true,
    filter: (s) => !/[\\/](node_modules|\.git)([\\/]|$)/.test(s),
  })
}

/**
 * The marketplace manifest carries an absolute path to this checkout, so it
 * is GENERATED here rather than committed — a checked-in copy would ship one
 * machine's path and break every other install.
 */
function writeMarketplaceManifest(dir) {
  const manifestPath = join(dir, 'marketplace.json')
  writeFileSync(manifestPath, JSON.stringify({
    name: MP_ID,
    description: 'Local marketplace for the dsh-routing-suite port.',
    plugins: [{
      name: PLUGIN,
      version: VERSION,
      description: manifest.description,
      author: manifest.author,
      category: 'developer-tools',
      source: { source: 'directory', path: SRC.replace(/\\/g, '/') },
    }],
  }, null, 2) + '\n', 'utf8')
}

console.log(`Installing ${PLUGIN}@${MP_ID} v${VERSION}`)
console.log(`  source: ${SRC}`)

copyInto(MP_DIR)
writeMarketplaceManifest(MP_DIR)
console.log(`  marketplace: ${MP_DIR}`)
copyInto(CACHE_DIR)
writeMarketplaceManifest(CACHE_DIR)
console.log(`  cache:       ${CACHE_DIR}`)

// Also materialize the manifest next to the source: the client records this
// checkout as the marketplace's directory source, so a fresh clone needs one
// generated here too. It is gitignored — it holds this machine's path.
writeMarketplaceManifest(SRC)
console.log(`  manifest:    ${join(SRC, 'marketplace.json')}`)

const now = new Date().toISOString()

editJson('plugins/known_marketplaces.json', (d) => {
  d.marketplaces ??= []
  const entry = {
    id: MP_ID,
    source: { source: 'directory', path: SRC.replace(/\\/g, '/') },
    name: MP_ID,
    description: `Local marketplace for ${PLUGIN}, a dsh-routing-suite port.`,
    addedAt: now,
    lastUpdated: now,
    pluginCount: 1,
  }
  const i = d.marketplaces.findIndex((m) => m.id === MP_ID)
  if (i >= 0) d.marketplaces[i] = { ...d.marketplaces[i], ...entry, addedAt: d.marketplaces[i].addedAt }
  else d.marketplaces.push(entry)
})

editJson('plugins/installed_plugins.json', (d) => {
  d.plugins ??= []
  const id = `${PLUGIN}@${MP_ID}`
  const entry = {
    id,
    name: PLUGIN,
    marketplace: MP_ID,
    version: VERSION,
    installPath: CACHE_DIR,
    installedAt: now,
    updatedAt: now,
    scope: 'user',
    source: { source: 'directory', path: SRC.replace(/\\/g, '/') },
  }
  const i = d.plugins.findIndex((p) => p.id === id)
  if (i >= 0) d.plugins[i] = { ...d.plugins[i], ...entry, installedAt: d.plugins[i].installedAt }
  else d.plugins.push(entry)
})

editJson('config.json', (d) => {
  d.plugins ??= {}
  d.plugins.enabledPlugins ??= {}
  d.plugins.enabledPlugins[`${PLUGIN}@${MP_ID}`] = true
})

console.log('\nDone. Open a NEW session for the hooks to take effect.')
console.log(`Config (optional): ${join(CLI, 'plugins', 'data', `${PLUGIN}@${MP_ID}`, 'config.json')}`)
