'use strict'
/**
 * 「状态与下一步」页（capability plan §14 第 7 条：跨模态闭环）。
 *
 * 这一页是唯一把三个能力闭环展示给用户的地方，因此它的**诚实性**比视觉更重要：
 *
 *   1. 缺失值必须显示「数据不足」，绝不显示 0（缺失 ≠ 0）；
 *   2. `insufficient_data` 必须如实显示为「记录不足，无法判断」，不得表述为有效；
 *   3. 记忆必须区分「正在影响建议」与「已记录但不影响决策」；
 *   4. 本页必须只读 —— 确认与写入一律交给小管家；
 *   5. 只使用 WebView 渲染器允许的动画方式，且每个可点元素都有点按反馈。
 *
 * 这些约束在 WXML 里是"某一行写法"，很容易在后续改动中被顺手抹掉，
 * 所以在这里用文本断言钉住。
 */
const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const root = path.join(__dirname, '..')
const read = file => fs.readFileSync(path.join(root, file), 'utf8')

const PAGE = 'pages/state/index'
const view = () => read(`${PAGE}.wxml`)
const script = () => read(`${PAGE}.js`)
const style = () => read(`${PAGE}.wxss`)

test('state page is registered, reachable and follows the first-paint contract', () => {
  const app = JSON.parse(read('app.json'))
  assert.ok(app.pages.includes(PAGE), '页面必须在 app.json 注册')
  const view0 = view()
  assert.match(view0, /^<include src="\/components\/page-transition\/first-paint\.wxml"\/>\r?\n<page-transition\/>/)
  assert.match(view0, /class="[^"]*\bui-page-content\b/)
  // 入口：首页新增的「状态与下一步」细条
  assert.match(read('pages/home/index.wxml'), /bindtap="goState"/)
  assert.match(read('pages/home/index.js'), /pages\/state\/index/)
})

test('missing values render as 数据不足 instead of 0', () => {
  const view0 = view()
  // 模板必须对 value === null 分支单独渲染，而不是直接输出数值。
  assert.match(view0, /\{\{item\.value === null\}\}[^<]*数据不足/)
  assert.match(view0, /wx:else class="state-row-value"/)
  // 数据层不得把 null 折算成 0。
  const js = script()
  assert.match(js, /if \(value === null \|\| value === undefined \|\| value === ''\) return null/)
  assert.doesNotMatch(js, /return 0\s*\/\/\s*missing|value\s*\|\|\s*0/, '缺失不得兜底为 0')
})

test('insufficient_data is never presented as a positive result', () => {
  const js = script()
  assert.match(js, /insufficient_data: '记录不足，无法判断'/)
  assert.match(js, /isInsufficient: item\.conclusion === 'insufficient_data'/)
  const view0 = view()
  assert.match(view0, /\{\{item\.isInsufficient \? 'weak' : 'ok'\}\}/)
  // 不得出现把 insufficient_data 说成"有效/达成"的措辞
  assert.doesNotMatch(view0, /已达成|效果良好|improved/)
})

test('memory is split into influential and recorded-but-ignored', () => {
  const js = script()
  assert.match(js, /memoryUsed/)
  assert.match(js, /memoryIgnored/)
  assert.match(js, /影响形态与强度/)
  assert.match(js, /仅作为陈述/)
  const view0 = view()
  assert.match(view0, /正在影响建议的长期记忆/)
  assert.match(view0, /已记录但不影响决策/)
  // 必须说明记忆不能改变安全边界
  assert.match(view0, /不能改变安全边界/)
})

test('the page is read-only and hands confirmation to the steward', () => {
  const js = script()
  // 只读：只允许 GET 读取三个只读接口
  const calls = js.match(/api\.(get|post|put|patch|del)\(/g) || []
  assert.deepEqual([...new Set(calls)], ['api.get('], '本页不得调用任何写接口')
  assert.match(js, /\/health\/state/)
  assert.match(js, /\/agent\/decision/)
  assert.match(js, /\/health\/outcomes/)
  // 确认路径：预填 chat 输入框（复用既有约定），不代替用户确认
  assert.match(js, /healthmate_insight_prompt/)
  assert.match(js, /pages\/chat\/index/)
  assert.doesNotMatch(js, /apply-plan/, '不得直接写库，写入必须由用户确认')
})

test('every tappable element carries the tap-feedback triad', () => {
  const view0 = view()
  const tappables = view0.match(/class="[^"]*\btappable\b[^"]*"[^>]*>/g) || []
  assert.ok(tappables.length >= 4, `可点元素过少，检查是否漏了入口: ${tappables.length}`)
  for (const tag of tappables) {
    assert.match(tag, /hover-class="tap/, `缺少 hover-class: ${tag}`)
    assert.match(tag, /hover-start-time="0"/, `缺少 hover-start-time: ${tag}`)
    assert.match(tag, /hover-stay-time="80"/, `缺少 hover-stay-time: ${tag}`)
  }
  // 基础类必须声明过渡，否则"松手"会瞬跳
  assert.match(read('app.wxss'), /\.tappable \{transition: opacity \.16s ease-out; \}/)
})

test('animations stay inside the WebView renderer contract', () => {
  const js = script()
  const style0 = style()
  // 渲染器闸门：WebView 下禁用 worklet / GSAP / DOM API
  for (const forbidden of ['worklet', 'SharedValue', 'applyAnimatedStyle', 'gsap', 'anime(', 'document', 'window.', 'requestAnimationFrame']) {
    assert.ok(!js.includes(forbidden), `JS 不得使用 ${forbidden}`)
    assert.ok(!style0.includes(forbidden), `WXSS 不得使用 ${forbidden}`)
  }
  // 只对 transform / opacity 做动画
  assert.match(style0, /@keyframes stateIn/)
  assert.match(style0, /transform: translateY\(12rpx\)/)
  assert.match(style0, /opacity: 0/)
  // 不得对不可动画属性写 transition
  const transitions = style0.match(/transition:[^;]+;/g) || []
  for (const item of transitions) {
    assert.ok(
      /opacity|transform|background-color|left|width|height/.test(item),
      `不可动画属性不得写 transition: ${item}`
    )
  }
})

test('displayed spacing stays on the 4rpx scale', () => {
  const style0 = style()
  const boxProps = style0.match(/(?:margin|padding|gap)[a-z-]*:\s*([^;]+);/g) || []
  const offenders = []
  for (const decl of boxProps) {
    const values = decl.match(/-?\d+rpx/g) || []
    for (const raw of values) {
      const n = Math.abs(parseInt(raw, 10))
      if (n === 0 || n > 48) continue // 0 与布局尺寸放行
      if (n % 4 !== 0) offenders.push(`${decl.trim()}`)
    }
  }
  assert.deepEqual(offenders, [], `脱离 4rpx 刻度的间距: ${offenders.join(' | ')}`)
})

test('labels match the backend decision weights', () => {
  const js = script()
  // 展示用的权重要与后端 decision.WEIGHTS 的键一致，否则会静默显示英文键名。
  const expected = [
    'expected_impact',
    'efficacy_from_history',
    'urgency',
    'plan_fit',
    'effort',
    'negative_feedback'
  ]
  for (const key of expected) {
    assert.ok(js.includes(`${key}:`), `缺少权重标签: ${key}`)
  }
})

test('no dead styles: every declared class is rendered somewhere', () => {
  const style0 = style()
  const view0 = view()
  const classes = style0.match(/\.[a-z][a-z0-9-]*/g) || []
  const declared = [...new Set(classes.map(item => item.slice(1)))]
  // 组合选择器里出现的类（如 .cons-tag.hard）也要能找到，取并集判断
  const missing = declared.filter(name => {
    if (name === 'state-page') return false // 页面根类由 class 组合使用
    return !view0.includes(name)
  })
  assert.deepEqual(missing, [], `WXSS 中未被 WXML 使用的类: ${missing.join(', ')}`)
})
