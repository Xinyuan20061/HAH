'use strict'
/**
 * WP0 red test — FOOD-01: `/pages/records/diet` was registered in app.json but
 * had zero navigation references anywhere in the product, so users could never
 * reach it (spec §3 FOOD-01, §9.3).
 *
 * The scan is intentionally dumb and text-based: any non-test JS/WXML/WXSS/JSON
 * occurrence of the route counts as an entry point.
 */
const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const root = path.join(__dirname, '..')

// Pages that are intentionally entry-less (deep links, tab hosts, dev shells).
const ENTRY_ALLOWLIST = new Set([
  'pages/home/index', // tabBar host
  'pages/chat/index' // tabBar host
])

function listFiles(dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) listFiles(full, out)
    else if (/\.(js|json|wxml|wxss|ts)$/.test(entry.name)) out.push(full)
  }
  return out
}

function routeReferences(route) {
  const needle = '/' + route
  const hits = []
  for (const file of listFiles(root)) {
    const rel = path.relative(root, file).replace(/\\/g, '/')
    if (rel.startsWith('tests/')) continue
    if (rel === 'app.json') continue // registration is not an entry point
    const text = fs.readFileSync(file, 'utf8')
    if (text.includes(needle)) hits.push(rel)
  }
  return hits
}

test('every registered page has at least one non-test navigation reference', () => {
  const app = JSON.parse(fs.readFileSync(path.join(root, 'app.json'), 'utf8'))
  const orphans = []
  for (const route of app.pages) {
    if (ENTRY_ALLOWLIST.has(route)) continue
    if (routeReferences(route).length === 0) orphans.push(route)
  }
  assert.deepEqual(orphans, [], `孤立页面（无任何入口）: ${orphans.join(', ')}`)
})

test('diet detail page is reachable from records and from a saved scan', () => {
  const recordsJs = fs.readFileSync(path.join(root, 'pages/records/index.js'), 'utf8')
  const recordsWxml = fs.readFileSync(path.join(root, 'pages/records/index.wxml'), 'utf8')
  const scanJs = fs.readFileSync(path.join(root, 'pages/scan/index.js'), 'utf8')

  // The records dashboard links to the diet ledger, and the ledger honours filters.
  assert.match(recordsJs, /pages\/records\/diet/)
  assert.match(recordsWxml, /查看明细/)
  assert.match(recordsJs, /meal_type/)

  // After a successful scan the page routes to the concrete saved record.
  assert.match(scanJs, /pages\/records\/diet/)
  assert.match(scanJs, /record\.id|recordId/)
})

test('diet ledger navigation is filterable per meal and per date', () => {
  const recordsJs = fs.readFileSync(path.join(root, 'pages/records/index.js'), 'utf8')
  assert.match(recordsJs, /mealType/)
  assert.match(recordsJs, /date/)
})
