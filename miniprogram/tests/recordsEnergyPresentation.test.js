const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const read = file => fs.readFileSync(path.join(__dirname, '..', file), 'utf8')

test('records page keeps the trend, horizontal meals, threshold and net balance in one component', () => {
  const view = read('pages/records/index.wxml')
  const style = read('pages/records/index.wxss')
  const script = read('pages/records/index.js')

  assert.match(view, /id="energyCanvas"/)
  assert.doesNotMatch(view, /七日能量趋势/)
  assert.doesNotMatch(view, /餐次摄入与运动消耗/)
  assert.doesNotMatch(view, /class="budget-state"/)
  assert.match(view, /target-pill \{\{targetExceeded \? 'over' : ''\}\}/)
  assert.match(view, /class="energy-overview/)
  assert.match(view, /class="meal-rows"/)
  assert.match(view, /class="meal-fill" style="\{\{item\.rowStyle\}\}"/)
  assert.match(view, /class="threshold-mini"/)
  assert.match(view, /动态摄入阈值/)
  assert.match(view, /class="net-inline"/)
  assert.doesNotMatch(view, /class="today-compact"/)

  for (const label of ['早餐', '午餐', '晚餐']) assert.match(script, new RegExp(label))
  assert.match(view, /bindtap="trends"/)
  assert.match(script, /trends\(\)\s*\{\s*wx\.navigateTo\(\{ url: '\/pages\/trends\/index' \}\)/)
  assert.match(script, /rowStyle: `width:\$\{width\.toFixed\(1\)\}%/)
  assert.match(style, /\.energy-overview\{[^}]*background:#fff;[^}]*transition:opacity \.16s ease-out/)
  assert.match(style, /\.meal-row\{[^}]*grid-template-columns:/)
  assert.match(style, /\.meal-fill\{[^}]*transform-origin:left;[^}]*transition:width/)
  assert.match(style, /\.threshold-copy\{[^}]*font-size:14rpx/)
  assert.match(style, /\.net-inline\{[^}]*text-align:right/)
  assert.match(style, /\.target-pill\.over\{[^}]*background:rgba\(192,57,43,\.12\);[^}]*color:#c0392b/)
  assert.match(style, /@keyframes mealRowIn/)
  assert.match(script, /targetExceeded: false/)
  assert.match(script, /exceeded: intake > upper/)
  assert.doesNotMatch(script, /处于今日建议区间/)

  const chart = read('utils/energyChart.js')
  assert.match(chart, /const compact = Boolean\(options\.compact\)/)
  assert.match(chart, /ctx\.lineTo\(x, y\)/)
  assert.match(chart, /strokeStyle = '#506336'/)
})

test('recording tools stay above analytics and use photo recognition with round actions', () => {
  const view = read('pages/records/index.wxml')
  const style = read('pages/records/index.wxss')
  const script = read('pages/records/index.js')

  assert.match(view, /bindtap="scan"[\s\S]{0,180}camera\.png/)
  assert.doesNotMatch(view, /bindtap="diet"/)
  assert.match(style, /\.quick-circle\{[^}]*width:82rpx;[^}]*height:82rpx;[^}]*border-radius:50%/)
  assert.match(script, /\/health\/energy-dashboard/)
  assert.match(script, /\/pages\/scan\/index/)
  assert.match(script, /budgetCopy\(today, target\)/)

  const toolsIndex = view.indexOf('class="quick-actions"')
  const overviewIndex = view.indexOf('class="energy-overview')
  assert.ok(toolsIndex >= 0 && toolsIndex < overviewIndex, 'recording tools must stay above energy analytics')
})

test('food photo save returns to the live records dashboard', () => {
  const script = read('pages/scan/index.js')
  assert.match(script, /wx\.redirectTo\(\{url:'\/pages\/records\/index'\}\)/)
})

test('records page rebinds the canvas after returning from seven-day trends', () => {
  const script = read('pages/records/index.js')

  assert.match(script, /onHide\(\) \{ this\.resetChart\(\) \}/)
  assert.match(script, /onUnload\(\) \{ this\._unloaded = true; this\.resetChart\(\) \}/)
  assert.match(script, /resetChart\(\) \{[\s\S]*this\._energyCharts = null[\s\S]*\}/)
  assert.match(script, /async load\(\) \{[\s\S]*this\.resetChart\(\)[\s\S]*this\.setData\(\{ loading: true/)
  assert.match(script, /this\._chartTimer = setTimeout\(\(\) => \{[\s\S]*this\.renderChart\(\)/)
})
