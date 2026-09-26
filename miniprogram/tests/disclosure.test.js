'use strict'
// Animated Text Disclosure 的语义守卫：
// 展开/收起必须是"从当前高度连续过渡到真实内容高度"，
// 而不是固定时长的淡入，也不是把内容 wx:if 掉再整体出现。
const test = require('node:test')
const assert = require('node:assert')
const fs = require('node:fs')
const path = require('node:path')

const ROOT = path.join(__dirname, '..')
const read = (p) => fs.readFileSync(path.join(ROOT, p), 'utf8')

const TARGETS = [
  { page: 'pages/scan/index', body: 'detail-panel', label: '饮食识别详情' },
  { page: 'pages/media/index', body: 'motion-details', label: '动作识别详情' },
]

test('详情展开使用真实内容高度过渡（Animated Text Disclosure）', () => {
  for (const t of TARGETS) {
    const wxml = read(t.page + '.wxml')

    // 1) 内容必须常驻：折叠态不能再靠 wx:if 卸载，否则无法量高度、也没有连续过渡
    assert.ok(
      !/wx:if="\{\{showDetails\}\}"[^>]*class="(detail-panel|motion-details)"/.test(wxml),
      `${t.label}: 详情内容不能再被 wx:if 卸载（那样只能整体出现，无法做高度过渡）`
    )

    // 2) 必须有高度载体 + 可测量的内容体
    assert.match(wxml, /class="disclosure \{\{showDetails\?'open':''\}\}"/, `${t.label}: 缺少 .disclosure 高度载体`)
    assert.match(wxml, /style="height:\{\{detailsHeight\}\}"/, `${t.label}: .disclosure 必须由 detailsHeight 驱动高度`)
    assert.match(wxml, /class="disclosure-body" id="detailsBody"/, `${t.label}: 缺少可测量的 #detailsBody`)
    assert.match(wxml, /bindtransitionend="onDisclosureEnd"/, `${t.label}: 缺少 transitionend 钩子（展开后要落成 auto）`)

    // 3) 标签必须闭合平衡（包裹层加了两层）
    const open = (wxml.match(/<view\b/g) || []).length
    const close = (wxml.match(/<\/view>/g) || []).length
    assert.strictEqual(open, close, `${t.label}: <view> 开合不平衡 (${open} vs ${close})`)
  }
})

test('展开/收起由测量驱动，且展开结束落成 auto', () => {
  for (const t of TARGETS) {
    const js = read(t.page + '.js')

    assert.match(js, /detailsHeight\s*:\s*'0px'/, `${t.label}: data 里缺少 detailsHeight 初值`)
    assert.match(js, /measureDetails\s*\(/, `${t.label}: 缺少 measureDetails —— 高度必须实测，不能写死`)
    assert.match(js, /createSelectorQuery\(\)[\s\S]{0,80}boundingClientRect/, `${t.label}: 必须用 boundingClientRect 量真实内容高度`)
    assert.match(js, /onDisclosureEnd\s*\(/, `${t.label}: 缺少 onDisclosureEnd`)
    assert.match(js, /detailsHeight !== 'auto'[\s\S]{0,80}detailsHeight: 'auto'/, `${t.label}: 展开后必须落成 auto，否则内容长高会被裁掉`)

    // 收起必须是"先量高度 → 再收到 0"，不能一步跳到 0
    assert.match(
      js,
      /measureDetails\(\(h\)\s*=>\s*\{\s*this\.setData\(\{\s*detailsHeight:\s*h \+ 'px'\s*\}[\s\S]{0,160}detailsHeight:\s*'0px'/,
      `${t.label}: 收起必须先量出当前高度再收到 0，否则没有连续过渡`
    )

    // 不能出现固定时长兜底（例如直接动画到 auto 或写死高度）
    assert.ok(!/detailsHeight:\s*'auto'\s*\}\s*\)\s*;\s*return/.test(js), `${t.label}: 展开不能直接跳到 auto`)
  }
})

test('高度过渡只作用于 height，不写不可动画属性', () => {
  const app = read('app.wxss')
  const m = app.match(/\.disclosure \{([^}]*)\}/)
  assert.ok(m, 'app.wxss 缺少 .disclosure')
  const body = m[1]
  assert.match(body, /overflow:\s*hidden/, '.disclosure 必须 overflow:hidden 才能裁出高度动画')
  assert.match(body, /transition:\s*height/, '.disclosure 必须对 height 过渡')

  const NON_ANIMATABLE = ['color', 'font-size', 'font-weight', 'font-family', 'line-height', 'letter-spacing', 'visibility', 'pointer-events']
  const hit = NON_ANIMATABLE.filter((p) => new RegExp(`(^|[\\s,])${p}([\\s,]|$)`).test(body))
  assert.deepStrictEqual(hit, [], '.disclosure 的过渡命中了不可动画属性: ' + hit.join(', '))
})
