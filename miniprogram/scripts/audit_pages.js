'use strict'
/**
 * Spec §6 page-level responsibility matrix: for every registered page, record
 * whether it exists, what APIs it calls, how many navigation references point at
 * it, and which Node test files mention it. Output is consumed by the P4
 * acceptance doc; the matrix is evidence, not a fixer.
 */
const fs = require('node:fs')
const path = require('node:path')

const root = path.join(__dirname, '..')
const outFile = process.argv[2] || path.join(root, 'results', 'page-audit-matrix.json')

function listFiles(dir, out = [], ext) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) listFiles(full, out, ext)
    else if (!ext || ext.test(entry.name)) out.push(full)
  }
  return out
}

const app = JSON.parse(fs.readFileSync(path.join(root, 'app.json'), 'utf8'))
const pageFiles = listFiles(path.join(root, 'pages'), [], /\.(js|wxml|wxss)$/)
const testFiles = listFiles(path.join(root, 'tests'), [], /\.test\.js$/)
const nonTestFiles = listFiles(root, [], /\.(js|wxml|wxss|json)$/)
  .filter(f => !f.replace(/\\/g, '/').includes('/tests/') && f.replace(/\\/g, '/') !== 'app.json')

const byRoute = route => {
  const base = path.join(root, route)
  const js = fs.existsSync(base + '.js') ? fs.readFileSync(base + '.js', 'utf8') : ''
  const wxml = fs.existsSync(base + '.wxml') ? fs.readFileSync(base + '.wxml', 'utf8') : ''
  const wxss = fs.existsSync(base + '.wxss') ? fs.readFileSync(base + '.wxss', 'utf8') : ''
  const jsLines = js.split(/\r?\n/).length
  const wxmlLines = wxml.split(/\r?\n/).length
  const wxssLines = wxss.split(/\r?\n/).length

  // API calls (write vs read), from the request util.
  const apiCalls = [...js.matchAll(/api\.(get|post|put|patch|del)\((['"`])(\/[^'"`]+)/g)].map(m => ({
    method: m[1], path: m[3]
  }))

  // Navigation references outside tests/app.json.
  const refs = []
  for (const f of nonTestFiles) {
    const rel = path.relative(root, f).replace(/\\/g, '/')
    if (fs.readFileSync(f, 'utf8').includes('/' + route)) refs.push(rel)
  }

  // Test files that mention this page.
  const tests = testFiles.filter(f => fs.readFileSync(f, 'utf8').includes(route)).map(f => path.basename(f))

  return {
    route,
    files: { js: jsLines > 0, wxml: wxmlLines > 0, wxss: wxssLines > 0 },
    lines: { js: jsLines, wxml: wxmlLines, wxss: wxssLines },
    entryRefs: refs,
    apiCalls,
    tests
  }
}

const matrix = app.pages.map(byRoute)
fs.mkdirSync(path.dirname(outFile), { recursive: true })
fs.writeFileSync(outFile, JSON.stringify(matrix, null, 2))
const summary = {
  totalPages: app.pages.length,
  complete: matrix.filter(r => r.files.js && r.files.wxml && r.files.wxss).length,
  orphanRoutes: matrix.filter(r => r.entryRefs.length === 0).map(r => r.route),
  untestedPages: matrix.filter(r => r.tests.length === 0).map(r => r.route),
  writePages: matrix.filter(r => r.apiCalls.some(c => c.method !== 'get')).map(r => ({
    route: r.route,
    writes: r.apiCalls.filter(c => c.method !== 'get')
  }))
}
fs.writeFileSync(path.join(path.dirname(outFile), 'page-audit-summary.json'), JSON.stringify(summary, null, 2))
console.log('matrix written to', outFile)
console.log(JSON.stringify(summary, null, 2))
