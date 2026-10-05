'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const root = path.join(__dirname, '..')
const read = file => fs.readFileSync(path.join(root, file), 'utf8')

test('functional pages inherit one persistent companion surface', () => {
  const app = JSON.parse(read('app.json'))
  const transitionView = read('components/page-transition/index.wxml')
  const transitionScript = read('components/page-transition/index.js')
  const transitionConfig = JSON.parse(read('components/page-transition/index.json'))

  assert.doesNotMatch(transitionScript, /showAgent/)
  assert.match(transitionView, /<agent-float\/>/)
  assert.equal(transitionConfig.usingComponents['agent-float'], '/components/agent-float/index')

  for (const page of app.pages) {
    const view = read(`${page}.wxml`)
    assert.match(view, /<page-transition/, `${page} must host the shared floating companion through page-transition`)
  }

  const floatScript = read('components/agent-float/index.js')
  assert.match(floatScript, /pages\/home\/index/)
  assert.match(floatScript, /pages\/chat\/index/)
})

test('floating companion opens chat and keeps voice input inside the composer', () => {
  const view = read('components/agent-float/index.wxml')
  const script = read('components/agent-float/index.js')
  const style = read('components/agent-float/index.wxss')

  assert.match(view, /class="agent-dialog"/)
  assert.match(view, /class="agent-trigger tappable"/)
  assert.match(view, /class="composer-voice tappable/)
  assert.match(view, /bindtouchstart="startVoice"/)
  assert.match(view, /bindtouchend="stopVoice"/)
  assert.match(view, /bindtouchcancel="cancelVoice"/)
  assert.match(view, /hover-start-time="0" hover-stay-time="80"/)
  assert.match(script, /healthmate_agent_id/)
  assert.match(script, /\/harness\/voice\/transcribe/)
  assert.match(script, /removeVoiceListeners\(\)/)
  assert.match(script, /offStart\(this\._onRecorderStart\)/)
  assert.match(script, /api\.streamPost\('\/agent\/respond\/stream'/)
  assert.match(script, /queueTokens\(chunk\)/)
  assert.match(script, /normalizeNavigation\(result\)/)
  assert.match(style, /\.agent-dialog\{[^}]*opacity:0;transform:translateY\(24rpx\) scale\(\.98\)/)
  assert.match(style, /\.agent-float\.expanded \.agent-dialog\{opacity:1;transform:translateY\(0\) scale\(1\)/)
  assert.match(style, /\.composer-voice\{[^}]*width:64rpx;[^}]*height:64rpx;[^}]*border-radius:50%/)
  assert.match(style, /\.dialog-send\{[^}]*width:64rpx;[^}]*height:64rpx;[^}]*border-radius:50%/)
  assert.match(style, /\.composer-voice image\{[^}]*width:32rpx;[^}]*height:32rpx/)
  assert.match(style, /\.dialog-send-arrow\{[^}]*width:36rpx;[^}]*height:36rpx/)
  assert.match(view, /class="dialog-send-arrow"/)
  assert.doesNotMatch(view, /dialog-send[^>]*\sdisabled=|\/assets\/icons\/send\.png/)
  assert.doesNotMatch(view, /class="agent-dock"[\s\S]*class="voice-trigger/)
  assert.doesNotMatch(style, /\.agent-float\{[^}]*animation:/)
})

test('plan uses one companion dialog for review, typing and voice', () => {
  const view = read('pages/plan/index.wxml')
  const floatScript = read('components/agent-float/index.js')
  const sessionScript = read('utils/floatingAgentSession.js')
  const config = JSON.parse(read('pages/plan/index.json'))
  assert.match(view, /<page-transition\/>/)
  assert.match(view, /page-owned="\{\{true\}\}"/)
  assert.match(view, /agent-id="\{\{previewActor\.id\}\}"/)
  assert.match(view, /plan-review="\{\{floatingReview\}\}"/)
  assert.match(view, /bindreviewconfirm="confirmPreview"/)
  assert.doesNotMatch(view, /agent-review-float|voice-only/)
  assert.match(floatScript, /function mergePlanReview/)
  assert.match(floatScript, /kind: 'plan-review'/)
  assert.match(sessionScript, /message\.ephemeral/)
  assert.equal(config.usingComponents['agent-float'], '/components/agent-float/index')
})

test('floating agent navigation is semantic and allow-listed', () => {
  const script = read('utils/agentNavigation.js')
  assert.match(script, /const NAVIGATION_TARGETS = \{/)
  assert.match(script, /plan_preview:/)
  assert.match(script, /capability_setup:/)
  assert.match(script, /healthmate_plan_preview_handoff:/)
  assert.match(script, /confirmation_required:\s*true/)
  assert.doesNotMatch(script, /navigation\.route|directive\.route/)
})
