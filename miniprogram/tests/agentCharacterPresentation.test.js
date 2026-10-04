'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const root = path.join(__dirname, '..')
const read = file => fs.readFileSync(path.join(root, file), 'utf8')

test('gym renders the full-body companion as a reusable semantic state machine', () => {
  const config = JSON.parse(read('pages/home/index.json'))
  const view = read('pages/home/index.wxml')
  const component = read('components/agent-character/index.js')
  const componentView = read('components/agent-character/index.wxml')

  assert.equal(config.usingComponents['agent-character'], '/components/agent-character/index')
  assert.match(view, /<agent-character[^>]*agent-id="\{\{activeCompanion\.id\}\}"/)
  assert.match(view, /activity="\{\{characterActivity\}\}"/)
  assert.match(view, /bindclipcomplete="onCharacterClipComplete"/)
  assert.doesNotMatch(view, /companion-avatar/, '旧圆头像不应继续充当角色舞台')

  for (const activity of ['idle', 'listening', 'thinking', 'planning', 'speaking', 'presenting', 'success', 'error']) {
    assert.ok(component.includes(`'${activity}'`), `角色状态机缺少 ${activity}`)
  }
  assert.match(component, /triggerEvent\('clipcomplete'/)
  assert.match(component, /const POSE_BY_ACTIVITY/)
  assert.match(component, /previousPoseIndex/)
  assert.match(component, /transitioning: true/)
  assert.match(component, /listening:\s*0/)
  assert.match(component, /function keepsIdleMotion\(activity\)/)
  assert.match(component, /activity === 'idle' \|\| activity === 'listening'/)
  assert.match(component, /xiaokang/)
  assert.match(componentView, /\/assets\/characters\/xiaojian-pixel-poses-v1\.png/)
  assert.match(componentView, /\/assets\/characters\/xiaokang-pixel-poses-v1\.png/)
  assert.match(componentView, /\/assets\/characters\/xiaojian-idle-curl-v3\.png/)
  assert.match(componentView, /\/assets\/characters\/xiaokang-idle-read-v5\.png/)
  assert.match(componentView, /\/assets\/characters\/xiaojian-gym-scene-v3\.png/)
  assert.match(componentView, /\/assets\/characters\/xiaokang-wellness-scene-v2\.png/)
  assert.match(componentView, /\/assets\/icons\/agent-xiaojian\.png/)
  assert.match(componentView, /\/assets\/icons\/agent-xiaokang\.png/)
  assert.doesNotMatch(componentView, /\.webp/)
  assert.match(componentView, /sprite-previous/)
  assert.match(componentView, /sprite-current/)
  assert.match(componentView, /showVoiceWave/)
})

test('character motion uses WebView-safe transform and opacity clips', () => {
  const css = read('components/agent-character/index.wxss')
  for (const clip of [
    'spriteEnterA', 'spriteEnterB', 'spriteExitA', 'spriteExitB',
    'xiaojianCurlFrames', 'xiaokangReadFrames', 'characterThink', 'characterPlan',
    'characterSpeak', 'characterPresent', 'characterSuccess', 'characterError'
  ]) {
    assert.match(css, new RegExp(`@keyframes ${clip}`), `缺少角色动画 ${clip}`)
  }
  assert.doesNotMatch(css, /transition[^;]*(?:color|font-size|font-weight|visibility)/)
  assert.doesNotMatch(css, /worklet|SharedValue|gsap|requestAnimationFrame/)
})

test('character stage stays visually quiet while listening', () => {
  const component = read('components/agent-character/index.js')
  const view = read('components/agent-character/index.wxml')
  const css = read('components/agent-character/index.wxss')

  assert.doesNotMatch(view, /character-aura|aura-back|aura-front/, '角色背后不应再渲染圆形光环')
  assert.doesNotMatch(css, /\.character-aura|\.aura-back|\.aura-front/, '不应保留废弃的光环样式')
  assert.doesNotMatch(css, /\.is-listening\s+\.sprite-motion\s*\{[^}]*animation/s, '语音输入时角色本体不应循环晃动')
  assert.doesNotMatch(css, /@keyframes\s+characterListen/, '不应保留倾听横移关键帧')
  assert.match(component, /keepsIdleMotion:\s*keepsIdleMotion\(nextActivity\)/)
  assert.match(view, /idle-sprite-motion \{\{keepsIdleMotion\?'is-visible':''\}\}/, '聆听应继续复用静息图集')
  assert.match(view, /sprite-motion \{\{keepsIdleMotion\?'':'is-visible'\}\}/, '聆听时不应切换到另一套姿态图')
  assert.doesNotMatch(view, /wx:if="\{\{showVoiceWave\}\}"/, '语音波形应常驻并用透明度过渡，避免挂载闪现')
  assert.match(view, /character-wave \{\{showVoiceWave\?'is-visible':''\}\}/, '倾听状态仍应保留克制的语音波形反馈')
  assert.match(css, /\.character-wave\s*\{[^}]*opacity:\s*0;[^}]*transition:\s*opacity \.2s ease-out, transform \.2s ease-out;/s)
  assert.match(css, /\.character-wave\.is-visible\s*\{[^}]*opacity:\s*1;/s)
})

test('idle companions inhabit distinct scenes with grounded frame animation', () => {
  const view = read('components/agent-character/index.wxml')
  const css = read('components/agent-character/index.wxss')
  const assets = [
    ['xiaojian-idle-curl-v3.png', 2048, 384, true],
    ['xiaokang-idle-read-v5.png', 2048, 384, true],
    ['xiaojian-gym-scene-v3.png', 768, 512, false],
    ['xiaokang-wellness-scene-v2.png', 768, 512, false]
  ]

  for (const [name, width, height, needsAlpha] of assets) {
    const file = path.join(root, 'assets', 'characters', name)
    const png = fs.readFileSync(file)
    assert.equal(png.readUInt32BE(16), width, `${name} 宽度错误`)
    assert.equal(png.readUInt32BE(20), height, `${name} 高度错误`)
    assert.match(view, new RegExp(`/assets/characters/${name.replace('.', '\\.')}`), `${name} 必须使用静态路径打包`)
    if (needsAlpha) assert.ok(png.includes(Buffer.from('tRNS')), `${name} 必须保留透明背景`)
  }

  assert.match(css, /\.character-scene\.scene-active\s*\{[^}]*opacity:\s*1/s)
  assert.match(css, /\.idle-curl-sheet\s*\{[^}]*xiaojianCurlFrames/s)
  assert.match(css, /\.idle-read-sheet\s*\{[^}]*xiaokangReadFrames/s)
  assert.match(css, /\.idle-sprite-sheet\s*\{[^}]*width:\s*2048rpx;[^}]*height:\s*384rpx;/s)
  assert.match(css, /\.idle-curl-sheet\s*\{[^}]*1\.6s\s+steps\(1,end\)/s)
  assert.match(css, /\.idle-read-sheet\s*\{[^}]*2\.4s\s+steps\(1,end\)/s)
  for (const offset of [
    'translateX(0)',
    'translateX(-256rpx)',
    'translateX(-512rpx)',
    'translateX(-768rpx)',
    'translateX(-1024rpx)',
    'translateX(-1280rpx)',
    'translateX(-1536rpx)',
    'translateX(-1792rpx)'
  ]) {
    assert.ok(css.includes(offset), `静息图集缺少帧位移 ${offset}`)
  }
  const idleFramesCss = css.slice(
    css.indexOf('@keyframes xiaojianCurlFrames'),
    css.indexOf('@keyframes characterThink')
  )
  assert.doesNotMatch(idleFramesCss, /translateY\(|translate\(/, '静息动画只能水平切帧，不能再触发纵向像素取整抖动')
  assert.doesNotMatch(css, /\.is-idle\s+\.sprite-motion\s*\{[^}]*animation/s, '空闲动作应逐帧播放，不应让整个角色漂移')
  assert.doesNotMatch(css, /\.character-floor\s*\{[^}]*animation/s, '接触阴影应固定，避免角色像悬浮在场景上')
})

test('home companion selector uses concise square portraits and dynamic spaces', () => {
  const view = read('pages/home/index.wxml')
  const script = read('pages/home/index.js')
  const css = read('pages/home/index.wxss')
  const portraits = ['xiaojian-portrait-v1.png', 'xiaokang-portrait-v1.png']

  assert.doesNotMatch(view, /choice-role|companion-greeting/)
  assert.doesNotMatch(script, /今天别给自己找借口|先听听身体|greeting:|role:/)
  assert.match(view, /\{\{activeCompanion\.space\}\}/)
  assert.match(script, /space:\s*'健身房'/)
  assert.match(script, /space:\s*'养生馆'/)
  assert.match(css, /\.choice-portrait\s*\{[^}]*width:\s*72rpx;[^}]*height:\s*72rpx;[^}]*border-radius:\s*18rpx/s)
  assert.match(css, /\.companion-stage\s*\{[^}]*padding:\s*24rpx;/s)
  assert.match(css, /\.stage-character\s*\{[^}]*margin-top:\s*16rpx;/s)

  for (const name of portraits) {
    const file = path.join(root, 'assets', 'characters', name)
    const png = fs.readFileSync(file)
    assert.equal(png.readUInt32BE(16), 256, `${name} 宽度错误`)
    assert.equal(png.readUInt32BE(20), 256, `${name} 高度错误`)
    assert.ok(png.includes(Buffer.from('tRNS')), `${name} 必须保留透明背景`)
    assert.match(script, new RegExp(`/assets/characters/${name.replace('.', '\\.')}`))
  }
})

test('gym consumes trusted presentation directives instead of guessing intent from text', () => {
  const script = read('pages/home/index.js')
  assert.match(script, /result\.presentation/)
  assert.match(script, /healthmate\.presentation\.v1/)
  assert.match(script, /'plan\.compose': 'planning'/)
  assert.match(script, /navigation\.mode === 'after_animation'/)
  assert.match(script, /plan_preview:\s*\{\s*route:\s*'\/pages\/plan\/index'/)
  assert.match(script, /mode=preview&run_id=/)
  assert.match(script, /function structuredPlanFallback/)
  assert.match(script, /result\.intent !== 'plan'/)
  assert.match(script, /AUTO_NAVIGATION_FALLBACK_MS/)
  assert.match(script, /queuePendingNavigation/)
  assert.match(script, /completePendingNavigation/)
  assert.match(script, /capability_setup/)
  assert.match(script, /const NAVIGATION_TARGETS/)
  assert.doesNotMatch(script, /function nextAction/)
  assert.doesNotMatch(script, /result\.ui_directive|result\.ui_directives/)
  assert.doesNotMatch(script, /\/记录\|饮食|\/训练\|动作/, '不得通过回复文案正则猜测路由')
})

test('gym keeps a short-lived structured handoff for backend version skew', () => {
  const home = read('pages/home/index.js')
  const plan = read('pages/plan/index.js')
  assert.match(home, /healthmate\.plan-handoff\.v1/)
  assert.match(home, /persistPlanHandoff\(result, presentation\.action\)/)
  assert.match(plan, /readPlanHandoff\(runId\)/)
  assert.match(plan, /PLAN_HANDOFF_MAX_AGE_MS/)
  assert.match(plan, /clearPlanHandoff\(this\.data\.previewRunId\)/)
})

test('gym never writes a generated plan and keeps preview confirmation explicit', () => {
  const script = read('pages/home/index.js')
  const view = read('pages/home/index.wxml')
  assert.doesNotMatch(script, /apply-plan/)
  assert.doesNotMatch(script, /api\.(?:post|put|patch|del)\([^\n]*plan/, '健身房不得直接写入计划')
  assert.match(view, /草案确认后才会加入计划/)
  assert.match(view, /bindtap="runResponseAction"/)
})

test('gym exposes a voice-free plan routing probe through the real agent decision pipeline', () => {
  const script = read('pages/home/index.js')
  const view = read('pages/home/index.wxml')
  const css = read('pages/home/index.wxss')

  assert.match(view, /bindtap="testPlanRouting"/)
  assert.match(view, />测试计划路由<\/view>/)
  assert.match(view, /route-test-button tappable/)
  assert.match(view, /hover-class="tap" hover-start-time="0" hover-stay-time="80"/)
  assert.match(script, /const PLAN_ROUTE_TEST_PROMPT = '给我制定一个计划'/)
  assert.match(script, /async submitAgentMessage\(text, options\)/)
  assert.match(script, /await this\.submitAgentMessage\(PLAN_ROUTE_TEST_PROMPT,\s*\{\s*channel: 'voice'/s)
  assert.match(script, /persistPlanHandoff\(result, presentation\.action\)/)
  assert.match(script, /queuePendingNavigation\(presentation\.autoNavigate \? presentation\.action : null\)/)
  const testHandler = script.match(/async testPlanRouting\(\)\s*\{[\s\S]*?\n  \},\n\n  async speak/) || []
  assert.ok(testHandler[0], '缺少计划路由测试处理器')
  assert.doesNotMatch(testHandler[0], /navigateAction\(/, '测试按钮不得绕过智能体决策直接跳页')
  assert.match(css, /\.route-test-button\s*\{[^}]*transition:opacity \.16s ease-out;/s)
})

test('new gym controls keep the tap feedback triad', () => {
  const view = read('pages/home/index.wxml')
  const tappables = view.match(/class="[^"]*\btappable\b[^"]*"[^>]*>/g) || []
  assert.ok(tappables.length >= 6)
  for (const tag of tappables) {
    assert.match(tag, /hover-class="tap|hover-class="voice-button-press/)
    assert.match(tag, /hover-start-time="0"/)
    assert.match(tag, /hover-stay-time="80"/)
  }
})

test('runtime pixel pose atlases stay lightweight and statically packageable', () => {
  const names = [
    'xiaojian-pixel-poses-v1.png',
    'xiaokang-pixel-poses-v1.png'
  ]
  let total = 0
  for (const name of names) {
    const file = path.join(root, 'assets', 'characters', name)
    assert.ok(fs.existsSync(file), `缺少运行时角色素材 ${name}`)
    const bytes = fs.statSync(file).size
    const png = fs.readFileSync(file)
    total += bytes
    assert.ok(bytes < 96 * 1024, `${name} 过大：${bytes} bytes`)
    assert.equal(png.readUInt32BE(16), 1024, `${name} 图集宽度应为 1024px`)
    assert.equal(png.readUInt32BE(20), 384, `${name} 图集高度应为 384px`)
    assert.equal(png[24], 8, `${name} 应使用 8-bit 通道`)
    assert.equal(png[25], 6, `${name} 应为 RGBA PNG`)
  }
  assert.ok(total < 192 * 1024, `两张像素角色图集总计过大：${total} bytes`)

  const componentView = read('components/agent-character/index.wxml')
  for (const name of names) {
    assert.match(componentView, new RegExp(`/assets/characters/${name.replace('.', '\\.')}`), `${name} 必须以静态路径出现在 WXML，避免真机打包时被判定为无依赖资源`)
  }
  assert.deepEqual(
    fs.readdirSync(path.join(root, 'assets', 'characters')).filter(name => name.endsWith('.webp')),
    [],
    '旧 WebP 不应继续占用小程序主包'
  )
})

test('pixel atlas geometry keeps four poses aligned without sliding transitions', () => {
  const css = read('components/agent-character/index.wxss')
  assert.match(css, /\.character-figure\s*\{[^}]*width:\s*256rpx;[^}]*height:\s*384rpx;/s)
  assert.match(css, /\.sprite-sheet\s*\{[^}]*width:\s*1024rpx;[^}]*height:\s*384rpx;/s)
  for (const [pose, offset] of [[0, '0'], [1, '-256rpx'], [2, '-512rpx'], [3, '-768rpx']]) {
    assert.match(css, new RegExp(`\\.pose-${pose} \\.sprite-sheet \\{ transform: translateX\\(${offset}\\); \\}`))
  }
  assert.doesNotMatch(css, /\.sprite-(?:current|previous)[^}]*transition\s*:/s, '姿态图集不能横向滑过中间帧')
})
