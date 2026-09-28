const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const read = file => fs.readFileSync(path.join(__dirname, '..', file), 'utf8')

test('records page makes the energy charts the primary interface', () => {
  const view = read('pages/records/index.wxml')
  assert.match(view, /id="energyCanvas"/)
  assert.match(view, /餐次摄入与运动消耗/)
  assert.match(view, /今日能量收支/)
  assert.match(view, /静息估算/)
  const script = read('pages/records/index.js')
  for (const label of ['早餐', '午餐', '晚餐']) assert.match(script, new RegExp(label))
  for (const label of ['运动消耗', '建议值']) assert.match(view, new RegExp(label))
})

test('records page uses photo recognition and round secondary actions', () => {
  const view = read('pages/records/index.wxml')
  const style = read('pages/records/index.wxss')
  const script = read('pages/records/index.js')
  assert.match(view, /bindtap="scan"[\s\S]{0,180}camera\.png/)
  assert.doesNotMatch(view, /bindtap="diet"/)
  assert.match(style, /\.quick-circle\{[^}]*width:82rpx;[^}]*height:82rpx;[^}]*border-radius:50%/)
  assert.match(script, /\/health\/energy-dashboard/)
  assert.match(script, /\/pages\/scan\/index/)
})

test('food photo save returns to the live records dashboard', () => {
  const script = read('pages/scan/index.js')
  assert.match(script, /wx\.switchTab\(\{url:'\/pages\/records\/index'\}\)/)
})
