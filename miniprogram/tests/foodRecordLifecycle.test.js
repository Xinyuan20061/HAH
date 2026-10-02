'use strict'
/**
 * WP0 red test — FOOD-02/03/05: the food record lifecycle on the client
 * (spec §6.1, §6.3, §6.5).
 *
 * Confirmed defects:
 *   * the scan page always submitted `meal_type: 'other'` and then bounced to
 *     the dashboard after a fixed 700 ms, so the user never saw what was saved;
 *   * the diet ledger was "manual add + last 8 rows" with no detail, edit or
 *     cursor, while the backend copy promised editing;
 *   * the ledger showed internal `meal_type` keys and had no conflict handling.
 */
const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const root = path.join(__dirname, '..')
const read = file => fs.readFileSync(path.join(root, file), 'utf8')

test('scan page asks the user to confirm the meal instead of hard-coding other', () => {
  const js = read('pages/scan/index.js')
  const wxml = read('pages/scan/index.wxml')

  assert.doesNotMatch(js, /meal_type:\s*'other'/, '识餐仍固定提交 meal_type=other')
  assert.match(js, /mealIndex|mealKeys/)
  assert.match(wxml, /bindchange="[^"]*[Mm]eal/)
  // Breakfast / lunch / dinner / snack must all be selectable.
  for (const label of ['早餐', '午餐', '晚餐', '加餐']) {
    assert.match(wxml, new RegExp(`range=|${label}`))
  }
})

test('scan page finishes on a saved card and links to the concrete record', () => {
  const js = read('pages/scan/index.js')
  const wxml = read('pages/scan/index.wxml')

  // Four honest steps: 识别 → 校正 → 确认 → 已保存 (no "入库" wording).
  assert.match(wxml, /已保存/)
  assert.doesNotMatch(wxml, /入库/)
  assert.match(js, /finalize/)
  // No unconditional 700ms bounce to the dashboard anymore.
  assert.doesNotMatch(js, /setTimeout\([^)]*redirectTo[\s\S]{0,80}700/)
  assert.match(js, /already_finalized/)
  assert.match(js, /record/)
})

test('scan page forces correction when the estimate is weak', () => {
  const js = read('pages/scan/index.js')
  // Low confidence, missing scale bar or high uncertainty must open the editor.
  assert.match(js, /confidence/)
  assert.match(js, /uncertainty_reasons/)
  assert.match(js, /portion_basis/)
  // Unrecognised input must offer a manual fallback, never invented nutrition.
  assert.match(js, /手动记录|manual/)
})

test('diet ledger uses cursor pagination, filters and per-row actions', () => {
  const js = read('pages/records/diet.js')
  const wxml = read('pages/records/diet.wxml')

  assert.match(js, /cursor/)
  assert.match(js, /has_more|hasMore/)
  assert.match(js, /meal_type|mealKeys/)
  assert.match(js, /PATCH|patch/)
  assert.match(wxml, /编辑/)
  assert.match(wxml, /删除/)
  // Manual nutrition templates are gone: the ledger is a ledger.
  assert.doesNotMatch(js, /templates/)
})

test('diet ledger shows provenance without provider or model internals', () => {
  const js = read('pages/records/diet.js')
  const wxml = read('pages/records/diet.wxml')
  const blob = js + wxml

  assert.match(blob, /图片估算/)
  assert.match(blob, /已人工修改|已确认/)
  for (const leak of ['deepseek', 'DeepSeek', 'confidence', 'model_id', 'provider']) {
    assert.doesNotMatch(blob, new RegExp(leak), `饮食明细页泄漏了内部字段 ${leak}`)
  }
})

test('diet ledger reports a version conflict instead of overwriting', () => {
  const js = read('pages/records/diet.js')
  assert.match(js, /version/)
  assert.match(js, /409|VERSION_CONFLICT|冲突/)
  assert.match(js, /刷新/)
})

test('saving, editing or deleting returns to a refreshed dashboard', () => {
  const recordsJs = read('pages/records/index.js')
  // onShow must re-fetch (the ledger edits happen on another page instance).
  assert.match(recordsJs, /onShow\s*\(\s*\)\s*\{[^}]*load\(/)
  const diet = read('pages/records/diet.js')
  assert.match(diet, /navigateBack|redirectTo/)
})

test('records dashboard distinguishes empty state from load failure', () => {
  const wxml = read('pages/records/index.wxml')
  assert.match(wxml, /state-card-error|error/)
  assert.match(wxml, /empty|还没有|暂无/)
})

test('request helper exposes PATCH and understands the unified error envelope', () => {
  const js = read('utils/request.js')
  assert.match(js, /patch\s*[:(]|method:\s*'PATCH'/)
  // New envelope: { error: { code, message, retryable, request_id, details } }
  assert.match(js, /body\.error/)
  assert.match(js, /retryable/)
  assert.match(js, /details/)
})
