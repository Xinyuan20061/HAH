const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const page = name => fs.readFileSync(path.join(__dirname, '..', 'pages', name, 'index.wxml'), 'utf8')

test('five primary views keep technical terms out of the default presentation', () => {
  const content = ['home', 'scan', 'media', 'plan', 'profile'].map(page).join('\n')
  for (const term of ['AI', '智能', '证据', '置信度', '不确定性', '规则偏差', '模型', 'VLM', '分析服务']) {
    assert.equal(content.includes(term), false, `unexpected term: ${term}`)
  }
})

test('food and motion result views retain correction, details, goals and six actions', () => {
  const scan = page('scan')
  const motion = page('media')
  assert.match(scan, /bindtap="toggleEdit"/)
  assert.match(scan, /bindtap="toggleDetails"/)
  assert.match(scan, /bindtap="save"/)
  assert.match(motion, /bindtap="toggleDetails"/)
  assert.match(motion, /设置今日目标/)
  const script = fs.readFileSync(path.join(__dirname, '..', 'pages', 'media', 'index.js'), 'utf8')
  for (const action of ['squat', 'pushup', 'lunge', 'leg_abduction', 'arm_abduction', 'arm_vw']) {
    assert.match(script, new RegExp(action))
  }
})

test('home and profile expose proactive health reminders', () => {
  const home = page('home')
  const profile = page('profile')
  assert.match(home, /bindtap="toInsights"/)
  assert.match(home, /健康提醒/)
  assert.match(profile, /bindtap="insights"/)
})
