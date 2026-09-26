#!/usr/bin/env node
/**
 * miniprogram UI 审计工具
 *
 * 用途：在做任何 WXSS / WXML 改动前后跑一次，暴露"肉眼看不见"的问题。
 * 与 miniprogram/tests/motionConstraints.test.js 的分工：
 *   - 本工具 = 诊断报告（容忍存量问题，默认退出码 0）
 *   - 那个测试 = 硬约束（必须在 CI 里恒定通过）
 *
 * 用法：
 *   node scripts/audit_miniprogram_ui.mjs              # 全量报告
 *   node scripts/audit_miniprogram_ui.mjs --strict     # 有 HIGH 问题则退出码 1
 *   node scripts/audit_miniprogram_ui.mjs --json       # 机器可读
 *   node scripts/audit_miniprogram_ui.mjs --page=home  # 只看某个页面
 */

import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, dirname, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const MP = join(ROOT, 'miniprogram')

const argv = process.argv.slice(2)
const STRICT = argv.includes('--strict')
const JSON_OUT = argv.includes('--json')
const PAGE_FILTER = (argv.find((a) => a.startsWith('--page=')) || '').split('=')[1] || ''

/* ------------------------------------------------------------------ *
 * 依据：skyline-wxss / references/animation.md
 * 官方明确列出"不支持 transition / animation"的属性。
 * 注意：该文档以 Skyline 为口径；WebView 渲染器下同样不建议依赖它们，
 * 因此这里作为告警而非绝对错误。
 * ------------------------------------------------------------------ */
const NON_ANIMATABLE = [
  'color', 'font-size', 'font-weight', 'font-style', 'font-family',
  'line-height', 'letter-spacing', 'word-spacing',
  'text-align', 'text-shadow', 'direction', 'white-space', 'word-break',
  'visibility', 'pointer-events'
]

/** 小程序逻辑层没有 DOM，禁止出现浏览器专有 API 或 Web 动画库。 */
const FORBIDDEN_JS = [
  { re: /\bgsap\b/i, why: 'GSAP 依赖 DOM，小程序逻辑层无 DOM' },
  { re: /\bTweenMax\b|\bTimelineMax\b|\bTweenLite\b/, why: 'GSAP 旧版 API，同上' },
  { re: /\bdocument\s*\./, why: '小程序逻辑层没有 document' },
  { re: /\bwindow\s*\./, why: '小程序逻辑层没有 window' },
  { re: /\bglobalThis\s*\.\s*document\b/, why: '同上' },
  { re: /\brequestAnimationFrame\s*\(/, why: '逻辑层无 rAF；Canvas 请用 canvas node 的 rAF' },
  { re: /\bgetBoundingClientRect\s*\(/, why: '请用 wx.createSelectorQuery()' }
]

/** 桌面时代的旧强调色（荧光柠檬绿系），HealthMate 3.0 已改用低饱和色板。 */
const LEGACY_ACCENTS = ['#d8ff84', '#d9ff84', '#d7ff84', '#d8ff83', '#b7e960', '#8fc45d', '#d6ff7f']

/**
 * 统一间距刻度（rpx，4 的倍数）。组件之间的间距只允许取这些值。
 * ≤ SPACING_MAX 才算"组件间距"；更大的数是布局尺寸（如 hero 高度），负数与 0 一律放行。
 * 卡片与卡片之间的纵向间距统一为 24rpx（见 app.wxss 的 .card）。
 */
const SPACING_SCALE = [4, 8, 12, 16, 20, 24, 32, 40, 48]
const SPACING_MAX = 48
const CARD_GAP = 24

const HIGH = 'HIGH'
const MED = 'MEDIUM'
const LOW = 'LOW'

const findings = []
const record = (severity, page, kind, message, detail) =>
  findings.push({ severity, page: page || '(app)', kind, message, detail: detail || '' })

/* ------------------------------------------------------------------ *
 * 文件发现
 * ------------------------------------------------------------------ */
function walk(dir) {
  const out = []
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, entry.name)
    if (entry.isDirectory()) out.push(...walk(p))
    else out.push(p)
  }
  return out
}

const allFiles = walk(MP)
const rel = (p) => relative(ROOT, p).replace(/\\/g, '/')
/** 页面唯一名：home / records / records/diet / settings/ai / profile/edit */
const pageName = (p) => {
  const r = rel(p)
  const m = r.match(/miniprogram\/pages\/(.+)\.(?:wxml|wxss|js)$/)
  if (!m) return 'pages'
  return m[1].replace(/\/index$/, '')
}

/* ------------------------------------------------------------------ *
 * 1. 样式表解析
 * ------------------------------------------------------------------ */

/** 去掉注释，避免注释里的 @keyframes / class 造成误报。 */
const stripComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '')

function parseWxss(text) {
  const css = stripComments(text)
  const declared = new Set()
  // 类选择器（含 .a.b 形式的复合选择器，逐段收集）
  for (const m of css.matchAll(/\.([A-Za-z_][\w-]*)/g)) declared.add(m[1])
  const keyframes = new Set()
  for (const m of css.matchAll(/@keyframes\s+([A-Za-z_][\w-]*)/g)) keyframes.add(m[1])
  const animationRefs = new Set()
  for (const m of css.matchAll(/(?:^|[;{\s])animation\s*:\s*([^;{}]+)/g)) {
    const name = firstAnimationName(m[1])
    if (name) animationRefs.add(name)
  }
  const animationNameDecls = new Set()
  for (const m of css.matchAll(/animation-name\s*:\s*([^;{}]+)/g)) {
    for (const tok of m[1].trim().split(/\s+/)) if (tok && tok !== 'none') animationNameDecls.add(tok)
  }
  const transitions = []
  for (const m of css.matchAll(/transition\s*:\s*([^;{}]+)/g)) transitions.push(m[1].trim())
  for (const m of css.matchAll(/transition-property\s*:\s*([^;{}]+)/g)) transitions.push(m[1].trim())
  const hexColors = new Set()
  for (const m of css.matchAll(/#[0-9a-fA-F]{3,8}\b/g)) hexColors.add(m[0].toLowerCase())
  // 形如 .progress view { ... width: ...% } 这类"进度条"规则缺少 transition
  const progressRulesWithoutTransition = []
  for (const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const sel = m[1].trim()
    const body = m[2]
    if (!/\bwidth\s*:/.test(body)) continue
    if (/\btransition\b/.test(body)) continue
    if (!/(track|progress|bar|fill|meter|gauge)/i.test(sel)) continue
    progressRulesWithoutTransition.push(sel)
  }
  // 间距刻度校验：组件间距必须落在统一刻度上，避免又退化成 5/7/13/17rpx 这种随手值
  const offScaleSpacing = new Set()
  const SPACING_RE = /(?:^|[;{\s])(?:margin|margin-top|margin-right|margin-bottom|margin-left|padding|padding-top|padding-right|padding-bottom|padding-left|gap|row-gap|column-gap)\s*:\s*([^;{}]+)/g
  for (const m of css.matchAll(SPACING_RE)) {
    for (const tok of m[1].trim().split(/\s+/)) {
      const v = (tok.match(/^(-?[\d.]+)rpx$/) || [])[1]
      if (v === undefined) continue
      const n = Number(v)
      // 0 / 负数（居中偏移）/ >48（布局尺寸）不算组件间距，放行
      if (n <= 0 || n > SPACING_MAX) continue
      if (!SPACING_SCALE.includes(n)) offScaleSpacing.add(n)
    }
  }
  return { declared, keyframes, animationRefs, animationNameDecls, transitions, hexColors, progressRulesWithoutTransition, offScaleSpacing: [...offScaleSpacing].sort((a, b) => a - b) }
}

const TIMING_KEYWORDS = /^(linear|ease|ease-in|ease-out|ease-in-out|step-start|step-end|infinite|normal|reverse|alternate|alternate-reverse|forwards|backwards|both|none|running|paused|inherit|initial)$/
function firstAnimationName(value) {
  for (const tok of value.trim().split(/\s+/)) {
    if (!tok) continue
    if (TIMING_KEYWORDS.test(tok)) continue
    if (/^[\d.]+m?s$/.test(tok)) continue
    if (/^(cubic-bezier|steps|linear)\s*\(/.test(tok)) continue
    if (tok.includes('(')) continue
    return tok
  }
  return null
}

/* ------------------------------------------------------------------ *
 * 2. WXML 类名提取（含 {{}} 内的字面量与动态前缀）
 * ------------------------------------------------------------------ */
function parseWxml(text) {
  const used = new Set()
  const dynamicPrefixes = new Set()
  const clean = text.replace(/<!--[\s\S]*?-->/g, '')

  for (const m of clean.matchAll(/class\s*=\s*"([^"]*)"/g)) {
    let value = m[1]
    // 记录 X-{{expr}} 形式的动态前缀
    for (const d of value.matchAll(/([A-Za-z_][\w-]*-)\{\{/g)) dynamicPrefixes.add(d[1])
    // {{...}} 内的字符串字面量视为候选类名
    for (const e of value.matchAll(/\{\{([\s\S]*?)\}\}/g)) {
      for (const s of e[1].matchAll(/'([^']*)'|"([^"]*)"/g)) {
        const lit = (s[1] ?? s[2] ?? '').trim()
        for (const tok of lit.split(/\s+/)) if (tok) used.add(tok)
      }
    }
    // 去掉 {{...}} 后剩下的静态 token
    value = value.replace(/\{\{[\s\S]*?\}\}/g, ' ')
    for (const tok of value.split(/\s+/)) {
      if (!tok) continue
      used.add(tok)
    }
  }
  return { used, dynamicPrefixes }
}

/* ------------------------------------------------------------------ *
 * 3. 执行检查
 * ------------------------------------------------------------------ */
const appWxssPath = join(MP, 'app.wxss')
const appWxss = parseWxss(readFileSync(appWxssPath, 'utf8'))

const pageDirs = []
for (const f of allFiles) {
  if (!f.endsWith('.wxml')) continue
  const page = pageName(f)
  if (PAGE_FILTER && !page.includes(PAGE_FILTER)) continue
  pageDirs.push({ page, wxml: f, wxss: f.replace(/\.wxml$/, '.wxss'), js: f.replace(/\.wxml$/, '.js') })
}

/**
 * 每个页面样式类由哪些页面定义。
 * 用途：区分两种"WXML 用了但本页没定义"的情况——
 *   - 别的页面定义过 → 明显是复制粘贴漏了样式（HIGH，一定没渲染出预期效果）
 *   - 哪个页面都没定义 → 可能是有意为之的钩子类（MEDIUM，需人判断）
 */
const classOwners = new Map()
for (const { page, wxss } of pageDirs) {
  let parsed
  try { parsed = parseWxss(readFileSync(wxss, 'utf8')) } catch { continue }
  for (const c of parsed.declared) {
    if (!classOwners.has(c)) classOwners.set(c, new Set())
    classOwners.get(c).add(page)
  }
}

const perPage = []
for (const { page, wxml, wxss, js } of pageDirs) {
  const hasWxss = (() => { try { return statSync(wxss).isFile() } catch { return false } })()
  const hasJs = (() => { try { return statSync(js).isFile() } catch { return false } })()
  const wxmlParsed = parseWxml(readFileSync(wxml, 'utf8'))
  const wxssParsed = hasWxss ? parseWxss(readFileSync(wxss, 'utf8')) : null

  const locallyDefined = wxssParsed ? wxssParsed.declared : new Set()
  const isCoveredByPrefix = (name) =>
    [...wxmlParsed.dynamicPrefixes].some((pre) => name.startsWith(pre))

  // --- WXML 用到但本页与 app.wxss 都没定义
  if (hasWxss) {
    const undefinedClasses = [...wxmlParsed.used].filter(
      (c) => !locallyDefined.has(c) && !appWxss.declared.has(c) && !isCoveredByPrefix(c)
    )
    // 其中"别的页面定义过"的一类，几乎必然是复制粘贴时漏带样式，必然渲染异常
    const crossPage = undefinedClasses.filter((c) => {
      const owners = classOwners.get(c)
      return owners && owners.size > 0 && !owners.has(page)
    })
    const nowhere = undefinedClasses.filter((c) => !crossPage.includes(c))
    if (crossPage.length) {
      for (const c of crossPage) {
        record(HIGH, page, 'cross-page-class',
          `类 .${c} 只定义在其他页面（${[...classOwners.get(c)].join(', ')}），本页渲染不会生效`,
          `WXML 使用了 class="${c}"，但 ${rel(wxss)} 与 app.wxss 都没有 .${c}`)
      }
    }
    if (nowhere.length) {
      record(MED, page, 'undefined-class',
        `${nowhere.length} 个类在任何 WXSS 中都没有定义（若非有意的钩子类，则渲染无样式）`,
        nowhere.join(', '))
    }
    // --- 死 CSS
    const { used, dynamicPrefixes } = wxmlParsed
    const dead = [...locallyDefined].filter(
      (c) => !used.has(c) && !appWxss.declared.has(c) && ![...dynamicPrefixes].some((p) => c.startsWith(p))
    )
    const ratio = locallyDefined.size ? dead.length / locallyDefined.size : 0
    if (dead.length) {
      record(ratio >= 0.3 ? MED : LOW, page, 'dead-css',
        `${dead.length}/${locallyDefined.size} 个页面样式类未被本页 WXML 使用（${Math.round(ratio * 100)}%）`,
        dead.slice(0, 40).join(', ') + (dead.length > 40 ? ` …(+${dead.length - 40})` : ''))
    }
    // --- 未定义关键帧
    const missingKf = [...wxssParsed.animationRefs, ...wxssParsed.animationNameDecls]
      .filter((n) => !wxssParsed.keyframes.has(n) && !appWxss.keyframes.has(n))
    if (missingKf.length) {
      record(HIGH, page, 'missing-keyframes',
        `animation 引用了未声明的 @keyframes`, missingKf.join(', '))
    }
    // --- 死关键帧
    const deadKf = [...wxssParsed.keyframes].filter(
      (n) => !wxssParsed.animationRefs.has(n) && !wxssParsed.animationNameDecls.has(n)
    )
    if (deadKf.length) {
      record(LOW, page, 'dead-keyframes', `${deadKf.length} 个 @keyframes 未被使用`, deadKf.join(', '))
    }
    // --- 不可动画属性
    for (const t of wxssParsed.transitions) {
      const hit = NON_ANIMATABLE.filter((p) => new RegExp(`(^|[\\s,])${p}([\\s,]|$)`).test(t))
      if (hit.length) {
        record(MED, page, 'non-animatable-transition',
          `对官方不支持的属性做过渡，不会生效`, `transition: ${t}  →  ${hit.join(', ')}`)
      }
    }
    // --- 进度条缺过渡
    for (const sel of wxssParsed.progressRulesWithoutTransition) {
      record(LOW, page, 'missing-progress-transition',
        `进度类规则设置 width 但没有 transition，数据刷新时会硬跳`, sel)
    }
    // --- 间距脱离统一刻度
    if (wxssParsed.offScaleSpacing.length) {
      record(MED, page, 'off-scale-spacing',
        `${wxssParsed.offScaleSpacing.length} 个间距值不在统一刻度上`,
        `偏离值 ${wxssParsed.offScaleSpacing.map((v) => v + 'rpx').join(', ')} ｜ 只允许 ${SPACING_SCALE.join('/')}rpx`)
    }
    // --- 旧强调色
    const legacy = [...wxssParsed.hexColors].filter((c) => LEGACY_ACCENTS.includes(c))
    if (legacy.length) {
      record(LOW, page, 'legacy-accent',
        `使用了 ${legacy.length} 个 HealthMate 3.0 已弃用的荧光强调色`, legacy.join(', '))
    }
  }

  // --- 可点元素缺交互反馈（hover-class）
  {
    const wxmlText = readFileSync(wxml, 'utf8')
    const noFeedback = []
    for (const m of wxmlText.matchAll(/<([a-zA-Z][\w-]*)((?:"[^"]*"|'[^']*'|[^>"'])*?)>/g)) {
      if (!/\b(?:bind|catch)tap\s*=/.test(m[2])) continue
      if (/hover-class\s*=/.test(m[2])) continue
      noFeedback.push('<' + m[1] + ' ' + m[2].replace(/\s+/g, ' ').trim().slice(0, 46) + '>')
    }
    if (noFeedback.length) {
      record(MED, page, 'missing-tap-feedback',
        `${noFeedback.length} 个可点元素没有 hover-class，点下去没有任何反馈`,
        `${noFeedback.slice(0, 6).join(' | ')} ｜ 加 hover-class="tap|tap-chip|tap-card|tap-btn" 与基础类 .tappable`)
    }
  }

  // --- JS 禁用模式
  if (hasJs) {
    const src = readFileSync(js, 'utf8')
    for (const { re, why } of FORBIDDEN_JS) {
      const m = src.match(re)
      if (m) record(HIGH, page, 'forbidden-js-api', `${why}（命中 "${m[0]}"）`)
    }
  }

  perPage.push({
    page,
    declared: wxssParsed ? wxssParsed.declared.size : 0,
    used: wxmlParsed.used.size,
    keyframes: wxssParsed ? wxssParsed.keyframes.size : 0
  })
}

// app.wxss 不在页面循环里，但它定义的 .card / .state-card 间距是全站基准，必须一起校验
if (appWxss.offScaleSpacing && appWxss.offScaleSpacing.length) {
  record(MED, '(app)', 'off-scale-spacing',
    `${appWxss.offScaleSpacing.length} 个间距值不在统一刻度上`,
    `偏离值 ${appWxss.offScaleSpacing.map((v) => v + 'rpx').join(', ')} ｜ 只允许 ${SPACING_SCALE.join('/')}rpx`)
}
// 全站卡片纵向间距应统一为 CARD_GAP
{
  const cardRule = (readFileSync(appWxssPath, 'utf8').match(/\.card\s*\{[^}]*\}/) || [''])[0]
  const gap = (cardRule.match(/margin-bottom:\s*(\d+)rpx/) || [])[1]
  if (gap && Number(gap) !== CARD_GAP) {
    record(MED, '(app)', 'card-gap-drift',
      `.card 的纵向间距是 ${gap}rpx，全站基准应为 ${CARD_GAP}rpx`,
      '所有卡片类容器都应等于 app.wxss 里 .card 的 margin-bottom')
  }
}

/**
 * 紧凑行检查（必须跨文件合并规则才准）：
 * 分隔线常写在 app.wxss（如 `.plan-simple .task{border-bottom}`），
 * 而 padding 写在页面 wxss（如 `.task{padding}`），单看一条规则永远发现不了。
 * 这里按"基类"（选择器里最后一个类）汇总全站规则再判断。
 */
{
  const rules = []
  for (const file of [...pageDirs.map((p) => p.wxss), appWxssPath]) {
    let css
    try { css = readFileSync(file, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '') } catch { continue }
    for (const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const sel = m[1].trim()
      if (sel.startsWith('@') || /%|from|to\b/.test(sel)) continue
      const classes = [...sel.matchAll(/\.([A-Za-z_][\w-]*)/g)].map((x) => x[1])
      if (!classes.length) continue
      rules.push({ base: classes[classes.length - 1], sel, body: m[2], file: rel(file) })
    }
  }
  const vpad = (body) => {
    const one = (prop) => {
      const m = body.match(new RegExp('(?:^|[;\\s])' + prop + '\\s*:\\s*([^;]+)'))
      if (!m) return null
      const first = m[1].trim().split(/\s+/)[0]
      return /^-?[\d.]+rpx$/.test(first) ? Number(first.replace('rpx', '')) : null
    }
    const t = one('padding-top') ?? one('padding')
    const b = one('padding-bottom') ?? one('padding')
    return (t || 0) + (b || 0)
  }
  const agg = new Map()
  for (const r of rules) {
    const a = agg.get(r.base) || { border: null, flex: false, pad: 0, minH: false, file: r.file }
    if (/border-(top|bottom)\s*:\s*[^;]*[1-9]\d*rpx/.test(r.body)) a.border = r.sel
    if (/display\s*:\s*flex/.test(r.body)) a.flex = true
    if (/min-height\s*:/.test(r.body)) a.minH = true
    a.pad = Math.max(a.pad, vpad(r.body))
    agg.set(r.base, a)
  }
  for (const [base, a] of agg) {
    if (a.border && a.flex && a.pad === 0 && !a.minH) {
      record(MED, a.file, 'cramped-row',
        `.${base} 有分隔线且是 flex 行，但没有上下内边距、也没有 min-height`,
        `分隔线来自 ${a.border} ｜ 列表行应至少有 24rpx 上下内边距或 min-height，否则内容会贴边、行与行挤在一起`)
    }
  }
}

/**
 * 卡片间距被"抹平"检查（records 第二/三行贴死就是这么来的）：
 * WXML 里带 card 的元素若自身下边距为 0（例如 .small{margin:0} 覆盖了 .card 的 24rpx），
 * 而它的父容器也没有下边距，它就会和下一个兄弟元素贴在一起。
 * 需要同时读 WXML 的结构与两个文件的样式，按"页面覆盖 app"的简化层叠判断。
 */
{
  const marginMaps = (css) => {
    const bottom = new Map()
    const top = new Map()
    for (const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const sel = m[1].trim()
      if (sel.startsWith('@') || /%|from|to\b/.test(sel)) continue
      const classes = [...sel.matchAll(/\.([A-Za-z_][\w-]*)/g)].map((x) => x[1])
      if (!classes.length) continue
      // 注意：CSS 里 0 通常不带单位（margin:0），不能只匹配 Nrpx
      const lenVal = (t) => {
        if (t === undefined) return null
        const s = String(t).trim()
        if (s === '0' || s === '-0') return 0
        return /^-?[\d.]+rpx$/.test(s) ? Number(s.replace('rpx', '')) : null
      }
      const body = m[2]
      const shorthand = body.match(/(?:^|[;\s])margin\s*:\s*([^;]+)/)
      const toks = shorthand ? shorthand[1].trim().split(/\s+/) : []
      const side = (name, shorthandIndex) => {
        const direct = body.match(new RegExp('(?:^|[;\\s])margin-' + name + '\\s*:\\s*([^;]+)'))
        if (direct) return lenVal(direct[1].trim().split(/\s+/)[0])
        if (!toks.length) return null
        if (shorthandIndex === 'top') return lenVal(toks[0])
        return lenVal(toks.length >= 3 ? toks[2] : toks[0])
      }
      const b = side('bottom', 'bottom')
      const t = side('top', 'top')
      for (const c of classes) {
        if (b !== null) bottom.set(c, b)
        if (t !== null) top.set(c, t)
      }
    }
    return { bottom, top }
  }
  const appMargins = marginMaps(readFileSync(appWxssPath, 'utf8'))
  const resolve = (pageMap, classes) => {
    let fromPage = null, fromApp = null
    for (const c of classes) {
      if (pageMap.bottom.has(c)) fromPage = pageMap.bottom.get(c)
      if (appMargins.bottom.has(c)) fromApp = appMargins.bottom.get(c)
    }
    return fromPage !== null ? fromPage : fromApp
  }
  const resolveTop = (pageMap, classes) => {
    let fromPage = null, fromApp = null
    for (const c of classes) {
      if (pageMap.top.has(c)) fromPage = pageMap.top.get(c)
      if (appMargins.top.has(c)) fromApp = appMargins.top.get(c)
    }
    return fromPage !== null ? fromPage : fromApp
  }
  const nodesOf = (text) => {
    const clean = text.replace(/<!--[\s\S]*?-->/g, '')
    const root = { tag: '(root)', classes: [], parent: null, children: [] }
    const stack = [root]
    const out = []
    const re = /<(\/?)([a-zA-Z][\w-]*)((?:"[^"]*"|'[^']*'|[^>"'])*?)(\/?)>/g
    let m
    while ((m = re.exec(clean))) {
      const [, close, tag, attrs, selfClose] = m
      if (close) { if (stack.length > 1) stack.pop(); continue }
      const cm = attrs.match(/class\s*=\s*"([^"]*)"/)
      const cls = new Set()
      if (cm) {
        for (const e of cm[1].matchAll(/\{\{[\s\S]*?\}\}/g)) {
          for (const s of e[0].matchAll(/'([^']*)'|"([^"]*)"/g)) { const t = (s[1] ?? s[2] ?? '').trim(); if (t) cls.add(t) }
        }
        for (const t of cm[1].replace(/\{\{[\s\S]*?\}\}/g, ' ').split(/\s+/)) if (t) cls.add(t)
      }
      const parent = stack[stack.length - 1]
      const node = { tag, classes: [...cls], parent, children: [] }
      parent.children.push(node)
      out.push(node)
      if (!selfClose) stack.push(node)
    }
    return { nodes: out, root }
  }
  const nextSibling = (node) => {
    if (!node || !node.parent) return null
    const sibs = node.parent.children
    return sibs[sibs.indexOf(node) + 1] || null
  }
  for (const { page, wxml, wxss } of pageDirs) {
    let wxmlText, pageCss
    try { wxmlText = readFileSync(wxml, 'utf8'); pageCss = readFileSync(wxss, 'utf8') } catch { continue }
    const pageMap = marginMaps(pageCss.replace(/\/\*[\s\S]*?\*\//g, ''))
    const { nodes } = nodesOf(wxmlText)
    for (const node of nodes) {
      if (!node.classes.includes('card')) continue
      const own = resolve(pageMap, node.classes)
      if (own === null || own > 0) continue
      const container = node.parent
      if (!container || container.tag === '(root)') continue
      // 容器自己有下边距 → 间距由它提供
      const containerMb = resolve(pageMap, container.classes)
      if (containerMb !== null && containerMb > 0) continue
      // 容器的下一个兄弟自带上边距 → 间距由下一个元素提供
      const sib = nextSibling(container)
      const sibMt = sib ? resolveTop(pageMap, sib.classes) : null
      if (sibMt !== null && sibMt > 0) continue
      // 容器是页面最后一个元素 → 下面没有东西
      if (!sib) continue
      record(MED, page, 'card-gap-suppressed',
        `带 card 的 <${node.tag} class="${node.classes.join(' ')}"> 下边距被置 0，容器 <${container.tag} class="${container.classes.join(' ')}"> 与下一个 <${sib.tag} class="${sib.classes.join(' ')}"> 都没有提供上下间距`,
        '会与下一个元素贴在一起 ｜ 给容器补 24rpx 下边距，或不要抹掉卡片自身的间距')
    }
  }
}

/* ------------------------------------------------------------------ *
 * 4. 输出
 * ------------------------------------------------------------------ */
if (JSON_OUT) {
  console.log(JSON.stringify({ pages: perPage, findings }, null, 2))
} else {
  const bySeverity = { [HIGH]: [], [MED]: [], [LOW]: [] }
  for (const f of findings) bySeverity[f.severity].push(f)

  const label = { [HIGH]: '🔴 HIGH', [MED]: '🟠 MED ', [LOW]: '🟡 LOW ' }
  console.log('\nminiprogram UI 审计  ·  %d 个页面', perPage.length)
  console.log('─'.repeat(74))
  for (const sev of [HIGH, MED, LOW]) {
    if (!bySeverity[sev].length) continue
    console.log(`\n${label[sev]}  (${bySeverity[sev].length})`)
    for (const f of bySeverity[sev]) {
      console.log(`  [${f.page}] ${f.kind}: ${f.message}`)
      if (f.detail) console.log(`      ${f.detail.slice(0, 220)}${f.detail.length > 220 ? ' …' : ''}`)
    }
  }
  if (!findings.length) console.log('\n✅ 没有发现问题。')
  const counts = [HIGH, MED, LOW].map((s) => `${s}=${bySeverity[s].length}`).join('  ')
  console.log('\n' + '─'.repeat(74))
  console.log(`合计 ${findings.length} 项  (${counts})`)
}

process.exit(STRICT && findings.some((f) => f.severity === HIGH) ? 1 : 0)
