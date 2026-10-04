const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const root = path.join(__dirname, '..')
const read = file => fs.readFileSync(path.join(root, file), 'utf8')

test('settings hub centralizes services preferences and account routes', () => {
  const app = JSON.parse(read('app.json'))
  const view = read('pages/settings/index.wxml')
  const script = read('pages/settings/index.js')

  assert.ok(app.pages.includes('pages/settings/index'))
  for (const label of ['文字模型', '语音服务', '默认陪伴', '自动播放回答', '饮食记录', '健康能力', '健康目标', '身体档案', '隐私与数据', '运行评测']) {
    assert.match(view, new RegExp(label))
  }
  assert.match(script, /healthmate_agent_id/)
  assert.match(script, /healthmate_voice_autoplay/)
  assert.match(script, /\/pages\/settings\/ai\/index\?section=voice/)
  assert.match(script, /dietRecords\(\)\s*\{\s*wx\.navigateTo\(\{ url: '\/pages\/records\/diet' \}\)/)
})

test('AI settings exposes text and voice providers in one configuration surface', () => {
  const view = read('pages/settings/ai/index.wxml')
  const script = read('pages/settings/ai/index.js')

  assert.match(view, /data-section="text"/)
  assert.match(view, /data-section="voice"/)
  for (const field of ['voice_api_key', 'voice_base_url', 'voice_stt_model', 'voice_tts_model', 'voice_name']) {
    assert.match(view, new RegExp(`data-k="${field}"`))
  }
  assert.match(script, /systemVoiceConfigured/)
})

test('AI settings no longer auto-runs voice-test; uses manual verify-once + server status', () => {
  const view = read('pages/settings/ai/index.wxml')
  const script = read('pages/settings/ai/index.js')

  // No automatic real-synthesis voice-test; the old endpoint must not be called.
  assert.doesNotMatch(script, /\/users\/me\/ai-config\/voice-test/)

  // Server-side status is read (no cloud call on the client) and surfaced.
  assert.match(script, /\/harness\/voice\/status/)
  assert.match(view, /上次验证/)
  assert.match(view, /last_verified_at/)

  // Explicit manual one-time connectivity entry points (not automatic).
  assert.match(script, /\/harness\/voice\/verify-once/)
  assert.match(script, /acknowledge_quota/)
  assert.match(view, /验证识别/)
  assert.match(view, /验证合成/)

  // Tencent cloud mode never collects SecretId/SecretKey on the client.
  assert.match(view, /腾讯云密钥由后端统一管理/)
  assert.doesNotMatch(view, /data-k="tencent_secret_(id|key)"/)
})

test('gym honours the settings voice autoplay preference', () => {
  const script = read('pages/home/index.js')
  assert.match(script, /healthmate_voice_autoplay/)
  assert.match(script, /if \(this\._voiceAutoplay\) this\.speak\(reply\)/)
})

test('capability page can focus the plan permission required by a companion request', () => {
  const script = read('pages/settings/capabilities/index.js')
  const view = read('pages/settings/capabilities/index.wxml')
  assert.match(script, /options\.focus === 'plan_outcome'/)
  assert.match(script, /#capability-\$\{this\.data\.focusPluginId\}/)
  assert.match(view, /id="capability-\{\{plugin\.plugin_id\}\}"/)
})
