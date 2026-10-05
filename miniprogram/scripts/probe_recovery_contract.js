'use strict'
/**
 * Recovery-contract probe: for every registered page, check whether the page
 * renders a loading state, an error state with a retry affordance, an empty
 * state, and (for write pages) busy-guard feedback. Prints a JSON report; used
 * to build the §9 recovery-contract test on evidence, not on assumptions.
 */
const fs = require('node:fs')
const path = require('node:path')
const root = path.join(__dirname, '..')
const app = JSON.parse(fs.readFileSync(path.join(root, 'app.json'), 'utf8'))

const rows = app.pages.map(route => {
  const jsPath = path.join(root, route + '.js')
  const wxmlPath = path.join(root, route + '.wxml')
  if (!fs.existsSync(jsPath) || !fs.existsSync(wxmlPath)) {
    return { route, missingFile: true }
  }
  const js = fs.readFileSync(jsPath, 'utf8')
  const wxml = fs.readFileSync(wxmlPath, 'utf8')
  return {
    route,
    hasLoading: /\{\{loading\}\}/.test(wxml),
    hasError: /error/.test(wxml),
    hasRetry: /bindtap="retry"/.test(wxml),
    hasEmpty: /暂无|还没有|没有记录|空|未绑定|空白/.test(wxml),
    writeCalls: (js.match(/api\.(post|put|patch|del)\(/g) || []).length,
    busyGuard: /\bbusy\b|_busy|disabled|submitting|saving/.test(js),
    toastFeedback: /showToast/.test(js)
  }
})

fs.writeFileSync(path.join(root, 'results', 'recovery-contract-probe.json'), JSON.stringify(rows, null, 2))
const missing = rows.filter(r => r.missingFile)
const noRetry = rows.filter(r => !r.missingFile && !r.hasRetry).map(r => r.route)
const writeNoGuard = rows.filter(r => !r.missingFile && r.writeCalls > 0 && !r.busyGuard).map(r => r.route)
const writeNoToast = rows.filter(r => !r.missingFile && r.writeCalls > 0 && !r.toastFeedback).map(r => r.route)
console.log(JSON.stringify({
  total: app.pages.length,
  missing,
  noRetry,
  writeNoGuard,
  writeNoToast
}, null, 2))
