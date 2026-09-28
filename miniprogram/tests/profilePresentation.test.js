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
  assert.match(view, /class="avatar-shell [^"]*\bbig\b"/)
  assert.match(style, /\.avatar-shell\{[^}]*width:128rpx;[^}]*min-width:128rpx;[^}]*max-width:128rpx;[^}]*height:128rpx;[^}]*min-height:128rpx;[^}]*max-height:128rpx;[^}]*border-radius:999rpx/)
  assert.match(style, /\.avatar-img,.avatar-text\{[^}]*width:100%;[^}]*height:100%;[^}]*border-radius:999rpx/)
  assert.match(style, /\.avatar-shell\.big\{[^}]*width:152rpx;[^}]*min-width:152rpx;[^}]*max-width:152rpx;[^}]*height:152rpx;[^}]*min-height:152rpx;[^}]*max-height:152rpx/)
  assert.match(style, /\.avatar-picker\{[^}]*position:absolute;[^}]*width:100%;[^}]*height:100%;[^}]*opacity:0/)
})

test('low frequency profile settings are compact round utilities', () => {
  const view = read('pages/profile/index.wxml')
  const style = read('pages/profile/index.wxss')
  assert.match(view, /class="utility-grid"/)
  for (const label of ['回答偏好', '隐私安全', '运行评测']) assert.match(view, new RegExp(label))
  assert.match(style, /\.utility-orb\{[^}]*width:72rpx;[^}]*height:72rpx;[^}]*border-radius:50%/)
})
