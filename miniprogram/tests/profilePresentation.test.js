const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const read = file => fs.readFileSync(path.join(__dirname, '..', file), 'utf8')

test('profile prioritizes identity, health summary and frequent actions', () => {
  const view = read('pages/profile/index.wxml')
  assert.match(view, /class="identity"/)
  assert.match(view, /class="facts-strip tappable"/)
  assert.match(view, /class="health-grid"/)
  assert.match(view, /class="goal-feature tappable"/)
  assert.match(view, /7 日趋势/)
  assert.match(view, /健康提醒/)
  assert.doesNotMatch(view, /class="menu-list"/)
  assert.doesNotMatch(view, /class="menu-row/)
})

test('profile avatars keep a circular one-to-one crop in both surfaces', () => {
  const view = read('pages/profile/index.wxml')
  const style = read('pages/profile/index.wxss')
  assert.match(view, /class="avatar-img"[^>]*mode="aspectFill"/)
  assert.match(view, /class="avatar-img big"[^>]*mode="aspectFill"/)
  assert.match(style, /\.avatar-btn\{[^}]*width:128rpx;[^}]*height:128rpx;[^}]*border-radius:50%/)
  assert.match(style, /\.avatar-img,.avatar-text\{[^}]*width:112rpx;[^}]*height:112rpx;[^}]*border-radius:50%/)
  assert.match(style, /\.avatar-img\.big,\.avatar-text\.big\{[^}]*width:136rpx;[^}]*height:136rpx;[^}]*border-radius:50%/)
})

test('low frequency profile settings are compact round utilities', () => {
  const view = read('pages/profile/index.wxml')
  const style = read('pages/profile/index.wxss')
  assert.match(view, /class="utility-grid"/)
  for (const label of ['回答偏好', '隐私安全', '运行评测']) assert.match(view, new RegExp(label))
  assert.match(style, /\.utility-orb\{[^}]*width:72rpx;[^}]*height:72rpx;[^}]*border-radius:50%/)
})
