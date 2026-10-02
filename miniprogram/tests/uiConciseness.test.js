const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const page = name => fs.readFileSync(path.join(__dirname, '..', 'pages', ...name.split('/'), 'index.wxml'), 'utf8')

test('high-frequency pages avoid redundant helper prose', () => {
  const plan = page('plan')
  const records = page('records')
  const profile = page('profile')
  const chat = page('chat')
  const goals = page('goals')

  assert.doesNotMatch(plan, /完成后也可以恢复|\{\{item\.desc\}\}/)
  assert.doesNotMatch(records, /拍照识餐后自动更新能量概览|占今日建议的比例/)
  assert.doesNotMatch(profile, /点击圆形头像|点击昵称输入框|低频设置|看最近变化/)
  assert.doesNotMatch(chat, /suggestion-hint/)
  assert.doesNotMatch(goals, /adaptive-desc|rule-policy|class="reason"/)
})

test('concise UI retains critical safety boundaries', () => {
  assert.match(page('plan'), /不适时请暂停训练/)
  // Spec §6.5: the scan page must keep calling the numbers an estimate draft
  // that is only written after confirmation.
  assert.match(page('scan'), /热量为估算草稿/)
  assert.match(page('insights'), /不把缺失数据当成异常/)
  assert.match(page('profile'), /不替代医生诊断/)
})
