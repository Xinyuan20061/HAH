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
  for (const label of ['文字模型', '语音服务', '默认陪伴', '自动播放回答', '健康目标', '身体档案', '隐私与数据', '运行评测']) {
    assert.match(view, new RegExp(label))
  }
  assert.match(script, /healthmate_agent_id/)
  assert.match(script, /healthmate_voice_autoplay/)
  assert.match(script, /\/pages\/settings\/ai\/index\?section=voice/)
})

test('AI settings exposes text and voice providers in one configuration surface', () => {
  const view = read('pages/settings/ai/index.wxml')
  const script = read('pages/settings/ai/index.js')

  assert.match(view, /data-section="text"/)
  assert.match(view, /data-section="voice"/)
  for (const field of ['voice_api_key', 'voice_base_url', 'voice_stt_model', 'voice_tts_model', 'voice_name']) {
    assert.match(view, new RegExp(`data-k="${field}"`))
  }
  assert.match(script, /\/users\/me\/ai-config\/voice-test/)
  assert.match(script, /systemVoiceConfigured/)
})

test('gym honours the settings voice autoplay preference', () => {
  const script = read('pages/home/index.js')
  assert.match(script, /healthmate_voice_autoplay/)
  assert.match(script, /if \(this\._voiceAutoplay\) this\.speak\(reply\)/)
})
