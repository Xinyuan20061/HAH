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
  const shellScript = fs.readFileSync(path.join(__dirname, '..', 'custom-tab-bar', 'index.js'), 'utf8')
  const shell = fs.readFileSync(path.join(__dirname, '..', 'custom-tab-bar', 'index.wxml'), 'utf8')
  assert.match(shellScript, /label:\s*'健康提醒'[\s\S]{0,90}route:\s*'\/pages\/insights\/index'/)
  assert.match(shell, /wx:for="\{\{quickItems\}\}"/)
  assert.match(home, /streak\.last7/)
  assert.match(profile, /bindtap="insights"/)
})

// 这两组样式曾长期存在于 WXSS 却从未接进 WXML（见 scripts/audit_miniprogram_ui.mjs 的 dead-css 报告），
// 因此在这里锁住"模板必须引用它们"，避免再次脱节。
test('food scan keeps the four-step flow rendered and driven by step', () => {
  const scan = page('scan')
  // Spec §6.5: the fourth step reads 已保存; "入库" implied a food warehouse.
  for (const label of ['识别', '校正', '确认', '已保存']) {
    assert.ok(scan.includes(label), `识餐流程缺少步骤文案：${label}`)
  }
  assert.match(scan, /class="flow"/)
  assert.match(scan, /\{\{step>=1\?/)
  assert.match(scan, /\{\{step>=4\?/)
})

test('food scan renders the calorie range bar from real data', () => {
  const scan = page('scan')
  assert.match(scan, /class="range-line"/)
  assert.match(scan, /rangeBar\.bandLeft/)
  assert.match(scan, /rangeBar\.pointPct/)
  const script = fs.readFileSync(path.join(__dirname, '..', 'pages', 'scan', 'index.js'), 'utf8')
  assert.match(script, /buildRangeBar/)
})
