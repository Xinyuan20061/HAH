const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const root = path.join(__dirname, '..')

test('tab bar uses a complete normal and selected icon set', () => {
  const app = JSON.parse(fs.readFileSync(path.join(root, 'app.json'), 'utf8'))
  assert.equal(app.tabBar.list.length, 5)
  for (const item of app.tabBar.list) {
    assert.ok(item.iconPath)
    assert.ok(item.selectedIconPath)
    assert.ok(fs.existsSync(path.join(root, item.iconPath)))
    assert.ok(fs.existsSync(path.join(root, item.selectedIconPath)))
  }
})

test('agent chat uses icon actions, token pacing and a stop state', () => {
  const script = fs.readFileSync(path.join(root, 'pages', 'chat', 'index.js'), 'utf8')
  const view = fs.readFileSync(path.join(root, 'pages', 'chat', 'index.wxml'), 'utf8')
  const style = fs.readFileSync(path.join(root, 'pages', 'chat', 'index.wxss'), 'utf8')
  const config = fs.readFileSync(path.join(root, 'config', 'index.js'), 'utf8')

  assert.match(script, /TOKEN_TICK_MS/)
  assert.match(script, /\/agent\/respond\/stream/)
  assert.match(script, /queueTokens/)
  assert.match(script, /stopGeneration/)
  assert.match(view, /assets\/icons\/settings\.png/)
  assert.match(view, /assets\/icons\/send\.png/)
  assert.match(view, /class="composer-input"/)
  assert.match(view, /class="thinking-dots"/)
  assert.match(view, /class="stop-mark"/)
  assert.match(style, /@keyframes thinkingDot/)
  assert.match(style, /@keyframes messageIn/)
  assert.match(style, /\.composer \{[^}]*border-radius:999rpx/)
  assert.match(style, /\.composer-input \{[^}]*flex:9/)
  assert.match(style, /\.composer-action \{[^}]*width:64rpx;[^}]*height:64rpx;[^}]*flex:0 0 64rpx;[^}]*border-radius:(?:50%|999rpx)/)
  assert.equal((view.match(/<view class="icon-copy\b/g) || []).length, 2)
  assert.doesNotMatch(view, /<button[^>]*class="[^"]*\bicon-copy\b/)
  assert.match(style, /\.icon-copy \{[^}]*width:52rpx;[^}]*max-width:52rpx;[^}]*height:52rpx;[^}]*max-height:52rpx;[^}]*flex:0 0 52rpx/)
  assert.match(config, /USE_STREAMING:\s*true/)
})
