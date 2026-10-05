'use strict'
/**
 * Spec §6 shared error-recovery contract: every registered page must survive a
 * failed load with an explicit recovery affordance, and every write action must
 * have a busy guard or user feedback. Allowlist entries are pages whose failure
 * surface is an alternative recovery path (re-analyze / re-send / regenerate)
 * or which have no network dependency at all (static or parameter-driven pages).
 *
 * The scan is deliberately dumb and text-based, mirroring
 * scripts/probe_recovery_contract.js so the contract is pinned in CI.
 */
const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const root = path.join(__dirname, '..')
const app = JSON.parse(fs.readFileSync(path.join(root, 'app.json'), 'utf8'))

// Pages whose recovery affordance is an alternative path, or with no load path.
const NO_RETRY_ALLOWLIST = new Set([
  'pages/media/index',          // 替代恢复：重新分析(reanalyze)
  'pages/chat/index',           // 替代恢复：重新发送消息
  'pages/plan/index',           // 替代恢复：重新生成计划
  'pages/workout/index',        // 纯静态动作库，无网络依赖
  'pages/exercise-detail/index',// 纯参数展示页，无网络依赖
  'pages/checkin/index',        // 打卡按钮页，写失败用 toast 反馈
  'pages/profile/edit',         // 表单页，写失败用 toast 反馈
  'pages/settings/ai/index'     // 表单页，写失败用 toast 反馈
])

function scan() {
  return app.pages.map(route => {
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
      writeCalls: (js.match(/api\.(post|put|patch|del)\(/g) || []).length,
      busyGuard: /\bbusy\b|_busy|disabled|submitting|saving|exporting|deleting/.test(js),
      toastFeedback: /showToast/.test(js),
      hasGet: /api\.get\(/.test(js)
    }
  })
}

test('all 28 registered pages have all three files', () => {
  const rows = scan()
  assert.equal(rows.length, 28, `页面数与 app.json 不一致: ${rows.length}`)
  assert.deepEqual(rows.filter(r => r.missingFile), [], '存在缺文件的页面')
})

test('every data-loading page renders an error branch (shared recovery contract)', () => {
  const rows = scan().filter(r => r.hasGet)
  const missing = rows.filter(r => !r.hasError)
    .filter(r => !NO_RETRY_ALLOWLIST.has(r.route))
  assert.deepEqual(
    missing.map(r => r.route),
    [],
    `有网络加载但无错误分支的页面: ${missing.map(r => r.route).join(', ')}`
  )
})

test('every error branch has a retry affordance or an allowlisted alternative', () => {
  const rows = scan()
  const missing = rows.filter(r => r.hasError && !r.hasRetry)
    .filter(r => !NO_RETRY_ALLOWLIST.has(r.route))
  assert.deepEqual(
    missing.map(r => r.route),
    [],
    `错误分支无重试且不在白名单的页面: ${missing.map(r => r.route).join(', ')}`
  )
})

test('every write page has a busy guard or user feedback', () => {
  const rows = scan().filter(r => r.writeCalls > 0)
  const missing = rows.filter(r => !r.busyGuard && !r.toastFeedback)
  assert.deepEqual(
    missing.map(r => r.route),
    [],
    `写页面无防连点且无反馈的页面: ${missing.map(r => r.route).join(', ')}`
  )
})
