'use strict'
// 交互动效的语义守卫。
// 这些测试不是"文件里有没有某个字符串"，而是守住容易在后续改动中被改坏的行为契约：
//   · 按下反馈不能因为新增 transition 而被盖掉
//   · 数值回弹的过渡必须写在基础类上，否则回落会瞬跳
//   · 弧形删除必须"先播完飞出动画再确认"，取消要能弹回，且不能劫持纵向滚动
const test = require('node:test')
const assert = require('node:assert')
const fs = require('node:fs')
const path = require('node:path')

const ROOT = path.join(__dirname, '..')
const read = (p) => fs.readFileSync(path.join(ROOT, p), 'utf8')

/* ── 交互 1：Expanding Tag Selection ─────────────────────────────────────── */
test('选中标签弹性放大并推开邻近标签，且不破坏按下反馈', () => {
  const css = read('pages/workout/index.wxss')
  const base = css.match(/\.chips text,\.chips view\{([^}]*)\}/)
  assert.ok(base, 'workout 缺少 .chips 基础样式')
  const b = base[1]

  // 让位靠 padding 增长（会触发重排，属于低频状态变化）
  assert.match(b, /transition:[^;]*padding/, '.chips 必须对 padding 过渡，否则邻近标签不会平滑让位')
  assert.match(b, /transition:[^;]*transform/, '.chips 必须对 transform 过渡')
  // 关键：局部 transition 会覆盖 .tappable 的基础过渡，必须把 opacity 一并声明
  assert.match(
    b,
    /transition:[^;]*opacity/,
    '.chips 覆盖了 .tappable 的 transition，必须一并声明 opacity，否则松手反馈会瞬跳'
  )

  const on = css.match(/\.chips text\.on,\.chips view\.on\{([^}]*)\}/)
  assert.ok(on, 'workout 缺少 .chips .on')
  const onPad = on[1].match(/padding:\s*(\d+)rpx\s+(\d+)rpx/)
  const basePad = b.match(/padding:\s*(\d+)rpx\s+(\d+)rpx/)
  assert.ok(onPad && basePad, 'padding 写法不合法')
  assert.ok(
    Number(onPad[2]) > Number(basePad[2]),
    `选中标签的横向 padding 必须大于未选中（${onPad[2]} vs ${basePad[2]}），否则不会把邻近标签推开`
  )
})

/* ── 交互 3：Velocity-Based Slider Snap ─────────────────────────────────── */
test('滑杆按松手速度补格，且补格不会越界', () => {
  const js = read('pages/goals/index.js')
  const wxml = read('pages/goals/index.wxml')

  assert.match(js, /const SNAP_VELOCITY\s*=\s*\d+/, 'goals 缺少速度阈值常量')
  assert.match(wxml, /bindchanging="slideLive"/, '卡片滑杆必须接 bindchanging 才能采样速度')
  assert.match(wxml, /bindchanging="checkinLive"/, '打卡滑杆必须接 bindchanging')

  // 越界时必须放弃补格，而不是把值推出范围
  assert.match(
    js,
    /next<min\)\s*return\{v,snapped:false\}/,
    '补格低于 min 时必须放弃'
  )
  assert.match(
    js,
    /next>max\)\s*return\{v,snapped:false\}/,
    '补格高于 max 时必须放弃'
  )

  // 速度必须在松手时被消费并清零，否则下一次点击会带着上一次的速度误补格
  assert.match(js, /consumeVelocity\(\)\{[^}]*this\._slideV=0/, '速度必须在消费后清零')
})

test('数值回弹的过渡写在基础类上（否则回落瞬跳）', () => {
  const css = read('pages/goals/index.wxss')
  const base = css.match(/\.target,\s*\.week-v\s*\{([^}]*)\}/)
  assert.ok(
    base,
    '过渡必须声明在 .target / .week-v 基础类上；只写在 .snap 里会让"放大有动画、回落瞬跳"'
  )
  assert.match(base[1], /transition:[^;]*transform/, '基础类必须对 transform 过渡')
  // .snap 只负责目标值
  assert.match(css, /\.snap\s*\{[^}]*transform:\s*scale/, '.snap 必须给出放大后的目标 transform')
})

/* ── 交互 4：Curved Card Deletion（行为级测试） ─────────────────────────── */
const swipeDelete = require('../utils/swipeDelete')

// 造一个最小的页面上下文，直接驱动手势方法，检查真实产出的 dragStyle / setData
function makeCtx() {
  const ctx = {
    data: {},
    setData(o) {
      Object.assign(this.data, o)
    },
    confirmRemove(id, done) {
      this.confirmed = { id, done }
    },
  }
  return Object.assign(ctx, swipeDelete())
}
const touch = (x, y) => ({ touches: [{ clientX: x, clientY: y }] })
const startAt = (ctx, x, y, id = 1) =>
  ctx.swipeDeleteStart({ ...touch(x, y), currentTarget: { dataset: { id } } })
const parseStyle = (s) => ({
  x: Number((s.match(/translateX\((-?[\d.]+)px\)/) || [])[1]),
  rot: Number((s.match(/rotate\((-?[\d.]+)deg\)/) || [])[1]),
  scale: Number((s.match(/scale\(([\d.]+)\)/) || [])[1]),
})

test('弧形轨迹：旋转角与缩放都随横向位移变化（不是直线平移）', () => {
  const ctx = makeCtx()
  startAt(ctx, 200, 300)
  const styles = []
  for (const x of [210, 245, 290]) {
    ctx.swipeDeleteMove(touch(x, 301))
    styles.push(parseStyle(ctx.data.dragStyle))
  }
  assert.ok(
    styles.every((s) => Number.isFinite(s.rot) && Number.isFinite(s.scale)),
    '轨迹里必须同时含可解析的旋转角与缩放'
  )
  assert.ok(styles[0].rot < styles[2].rot, `旋转角必须随位移增大：${styles.map((s) => s.rot).join(' / ')}`)
  assert.ok(styles[0].scale > styles[2].scale, `缩放必须随位移加深：${styles.map((s) => s.scale).join(' / ')}`)
  assert.ok(styles[0].x < styles[2].x, '横向位移必须跟随手指')
})

test('纵向手势不产生任何位移，把滚动交还页面', () => {
  const ctx = makeCtx()
  startAt(ctx, 200, 300)
  ctx.swipeDeleteMove(touch(202, 360)) // 主要往纵向走
  assert.strictEqual(ctx.data.dragStyle, '', '纵向手势不能产生横向位移')
})

test('未达阈值松手 → 弹回，且绝不触发删除确认', () => {
  const ctx = makeCtx()
  startAt(ctx, 200, 300)
  ctx.swipeDeleteMove(touch(230, 301))
  ctx.swipeDeleteEnd()
  assert.strictEqual(ctx.data.dragId, '', '未达阈值必须清掉拖动态（回到原位）')
  assert.strictEqual(ctx.data.dragStyle, '', '未达阈值必须清掉内联 transform')
  assert.strictEqual(ctx.confirmed, undefined, '未达阈值不能触发删除确认')
})

test('超过阈值 → 先播完飞出动画，动画结束后才弹确认框', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const ctx = makeCtx()
  startAt(ctx, 200, 300)
  ctx.swipeDeleteMove(touch(320, 301)) // 120px > 90px 阈值
  ctx.swipeDeleteEnd()

  assert.strictEqual(ctx.data.leavingId, 1, '判定删除后必须进入飞出态')
  assert.notStrictEqual(ctx.data.dragStyle, '', '飞出态必须有外推的 transform')
  assert.strictEqual(ctx.confirmed, undefined, '飞出动画播完之前不能弹确认框')

  t.mock.timers.tick(1000)
  assert.ok(ctx.confirmed, '飞出动画结束后必须走删除确认流程')
  assert.strictEqual(ctx.confirmed.id, 1, '确认流程必须带上被滑出的那条记录 id')
})

test('在确认框里取消 → 卡片弹回原位', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const ctx = makeCtx()
  startAt(ctx, 200, 300)
  ctx.swipeDeleteMove(touch(320, 301))
  ctx.swipeDeleteEnd()
  t.mock.timers.tick(1000)

  ctx.confirmed.done() // 模拟用户点了"取消"
  assert.strictEqual(ctx.data.dragId, '', '取消后必须清掉拖动态')
  assert.strictEqual(ctx.data.leavingId, '', '取消后必须清掉飞出态（卡片弹回）')
  assert.strictEqual(ctx.data.dragStyle, '', '取消后必须清掉内联 transform')
})

test('touchcancel 一律弹回，不触发删除', () => {
  const ctx = makeCtx()
  startAt(ctx, 200, 300)
  ctx.swipeDeleteMove(touch(340, 301))
  ctx.swipeDeleteCancel()
  assert.strictEqual(ctx.data.leavingId, '', 'touchcancel 不能留在飞出态')
  assert.strictEqual(ctx.data.dragId, '', 'touchcancel 必须清掉拖动态')
  assert.strictEqual(ctx.confirmed, undefined, 'touchcancel 不能触发删除')
})

test('页面侧接线完整：挂载手势、绑定事件、且不用 catch 吃掉滚动', () => {
  for (const kind of ['diet', 'exercise']) {
    const js = read(`pages/records/${kind}.js`)
    assert.match(js, /\.\.\.swipeDelete\(\)/, `${kind} 未挂载 swipeDelete`)
    assert.match(js, /confirmRemove\(id,\s*done\)/, `${kind} 缺少可回调的 confirmRemove`)
    // Whitespace/brace style is not the contract: the cancel branch must hand
    // control back to swipeDelete so the card snaps home.
    assert.match(js, /res\.confirm[\s\S]{0,200}done\(\)/, `${kind} 在确认框取消时必须调用 done() 把卡片弹回`)

    const wxml = read(`pages/records/${kind}.wxml`)
    for (const ev of [
      'bindtouchstart="swipeDeleteStart"',
      'bindtouchmove="swipeDeleteMove"',
      'bindtouchend="swipeDeleteEnd"',
      'bindtouchcancel="swipeDeleteCancel"',
    ]) {
      assert.ok(wxml.includes(ev), `${kind} 卡片缺少 ${ev}`)
    }
    // 必须用 bind 而不是 catch —— catchtouchmove 会把页面滚动一起吃掉
    assert.ok(!wxml.includes('catchtouchmove'), `${kind} 不能用 catchtouchmove，否则整页无法滚动`)
    // 静止时不应有残留 inline transform
    assert.match(wxml, /style="\{\{item\.id===dragId\?dragStyle:''\}\}"/, `${kind} 内联 transform 必须只在拖动时存在`)
  }
})

test('拖动态与飞出态的过渡都写在基础类上', () => {
  for (const kind of ['diet', 'exercise']) {
    const css = read(`pages/records/${kind}.wxss`)
    assert.match(css, /\.record\s*\{[^}]*transition:[^;]*transform/, `${kind}: .record 必须有 transform 过渡（回弹才不瞬跳）`)
    assert.match(css, /\.dragging\s*\{[^}]*transition:\s*none/, `${kind}: .dragging 必须关掉过渡，否则跟手有拖尾延迟`)
    assert.match(css, /\.leaving\s*\{[^}]*opacity:\s*0/, `${kind}: .leaving 必须淡出`)
  }
})
