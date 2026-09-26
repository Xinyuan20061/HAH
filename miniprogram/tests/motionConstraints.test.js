const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const MP = path.join(__dirname, '..')
const ROOT = path.join(MP, '..')

function walk(dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name)
    if (entry.isDirectory()) walk(p, out)
    else out.push(p)
  }
  return out
}
const read = (p) => fs.readFileSync(p, 'utf8')
const rel = (p) => path.relative(ROOT, p).replace(/\\/g, '/')

const wxssFiles = () => walk(MP).filter((f) => f.endsWith('.wxss'))
const codeFiles = () => walk(MP).filter((f) => f.endsWith('.js') && !f.includes(`${path.sep}tests${path.sep}`))

/**
 * 小程序逻辑层没有 DOM/BOM。任何"浏览器专有 API 或 Web 动画库"都不可能工作，
 * 一旦引入就是运行期必错或静默失效。这里在测试层直接封死。
 */
test('miniprogram logic layer stays free of DOM-only APIs and web animation libraries', () => {
  const FORBIDDEN = [
    [/\bgsap\b/i, 'GSAP 需要 DOM，小程序逻辑层没有 DOM'],
    [/\bTweenMax\b|\bTweenLite\b|\bTimelineMax\b/, 'GSAP 旧版 API，同上'],
    [/\bdocument\s*\./, '逻辑层没有 document'],
    [/\bwindow\s*\./, '逻辑层没有 window'],
    [/\brequestAnimationFrame\s*\(/, '逻辑层没有 rAF（Canvas node 的 rAF 例外，需显式取 node）'],
    [/\bgetBoundingClientRect\s*\(/, '请改用 wx.createSelectorQuery()']
  ]
  const violations = []
  for (const file of codeFiles()) {
    const src = read(file)
    for (const [re, why] of FORBIDDEN) {
      const hit = src.match(re)
      if (hit) violations.push(`${rel(file)}: "${hit[0]}" — ${why}`)
    }
  }
  assert.deepEqual(violations, [], `发现浏览器专有 API：\n${violations.join('\n')}`)
})

/**
 * animation 引用了不存在的 @keyframes 时，动画会完全不动且不报错。
 * 声明可以来自页面 wxss 或 app.wxss。
 */
test('every referenced @keyframes animation is declared', () => {
  const TIMING = /^(linear|ease|ease-in|ease-out|ease-in-out|step-start|step-end|infinite|normal|reverse|alternate|alternate-reverse|forwards|backwards|both|none|running|paused|inherit|initial)$/
  const declared = new Set()
  for (const file of wxssFiles()) {
    const css = read(file).replace(/\/\*[\s\S]*?\*\//g, '')
    for (const m of css.matchAll(/@keyframes\s+([A-Za-z_][\w-]*)/g)) declared.add(m[1])
  }
  const referenced = new Map()
  for (const file of wxssFiles()) {
    const css = read(file).replace(/\/\*[\s\S]*?\*\//g, '')
    const collect = (value) => {
      for (const tok of value.trim().split(/\s+/)) {
        if (!tok || TIMING.test(tok)) continue
        if (/^[\d.]+m?s$/.test(tok) || tok.includes('(')) continue
        if (!referenced.has(tok)) referenced.set(tok, rel(file))
        return
      }
    }
    for (const m of css.matchAll(/(?:^|[;{\s])animation\s*:\s*([^;{}]+)/g)) collect(m[1])
    for (const m of css.matchAll(/animation-name\s*:\s*([^;{}]+)/g)) {
      for (const tok of m[1].trim().split(/\s+/)) {
        if (tok && tok !== 'none' && !referenced.has(tok)) referenced.set(tok, rel(file))
      }
    }
  }
  const missing = [...referenced].filter(([name]) => !declared.has(name))
  assert.deepEqual(
    missing.map(([name, where]) => `${where}: animation "${name}" 没有对应的 @keyframes`),
    []
  )
})

/**
 * 官方明确列出不支持 transition/animation 的属性（见 skyline-wxss/references/animation.md）。
 * 对这些属性写过渡不会生效，属于"写了以为生效"的隐性 bug。
 */
test('transition does not target properties that cannot be animated', () => {
  const NON_ANIMATABLE = [
    'color', 'font-size', 'font-weight', 'font-style', 'font-family',
    'line-height', 'letter-spacing', 'word-spacing',
    'text-align', 'text-shadow', 'direction', 'white-space', 'word-break',
    'visibility', 'pointer-events'
  ]
  const violations = []
  for (const file of wxssFiles()) {
    const css = read(file).replace(/\/\*[\s\S]*?\*\//g, '')
    for (const m of css.matchAll(/transition(?:-property)?\s*:\s*([^;{}]+)/g)) {
      const value = m[1].trim()
      // 简写 transition: .2s 等价于 all，不针对具体不可动画属性，跳过
      const hit = NON_ANIMATABLE.filter((p) => new RegExp(`(^|[\\s,])${p}([\\s,]|$)`).test(value))
      if (hit.length) violations.push(`${rel(file)}: transition: ${value} → ${hit.join(', ')}`)
    }
  }
  assert.deepEqual(violations, [], `对不可动画属性做过渡：\n${violations.join('\n')}`)
})

/**
 * 渲染器闸门。
 * 项目当前跑在 WebView 渲染器上（skylineRenderEnable=false、compileWorklet=false），
 * 此时 worklet / SharedValue 之类的 API 完全不可用。如果哪天开启 Skyline，
 * 这个测试会自动放行——闸门跟着配置走，而不是靠人记得。
 */
test('worklet APIs stay out of page code while the Skyline renderer is disabled', () => {
  const projectConfig = JSON.parse(read(path.join(MP, 'project.config.json')))
  const privateConfig = JSON.parse(read(path.join(MP, 'project.private.config.json')))
  const appJson = JSON.parse(read(path.join(MP, 'app.json')))

  const compileWorklet = Boolean(projectConfig.setting && projectConfig.setting.compileWorklet)
  const skylineEnabled = Boolean(privateConfig.setting && privateConfig.setting.skylineRenderEnable)
  const declaredRenderer = appJson.renderer || (appJson.rendererOptions && appJson.rendererOptions.default)

  if (skylineEnabled && compileWorklet && declaredRenderer === 'skyline') return // 已迁移，放行

  const WORKLET_ONLY = [
    /\bSharedValue\b/, /\buseSharedValue\b/, /\bapplyAnimatedStyle\b/,
    /\brunOnUI\b/, /\brunOnJS\b/, /wx\.worklet/, /\bworklet\s*:/
  ]
  const violations = []
  for (const file of walk(MP).filter((f) => /\.(js|wxml)$/.test(f) && !f.includes(`${path.sep}tests${path.sep}`))) {
    const src = read(file)
    for (const re of WORKLET_ONLY) {
      const hit = src.match(re)
      if (hit) violations.push(`${rel(file)}: "${hit[0]}"`)
    }
  }
  assert.deepEqual(violations, [],
    'Skyline/Worklet 未启用（skylineRenderEnable=false, compileWorklet=false, app.json 无 renderer:skyline），' +
    `这些 API 不会生效：\n${violations.join('\n')}`)
})

/** 导航栏底色必须与页面底色一致，否则下拉回弹时会露出色差。 */
test('navigation bar background matches the page background token', () => {
  const appJson = JSON.parse(read(path.join(MP, 'app.json')))
  const pageBg = read(path.join(MP, 'app.wxss')).match(/page\s*\{[^}]*background\s*:\s*([^;]+);/)
  assert.ok(pageBg, 'app.wxss 未定义 page 背景色')
  assert.equal(
    String(appJson.window.navigationBarBackgroundColor).toLowerCase(),
    pageBg[1].trim().toLowerCase(),
    'app.json window.navigationBarBackgroundColor 与 app.wxss 的 page 背景色不一致'
  )
})
