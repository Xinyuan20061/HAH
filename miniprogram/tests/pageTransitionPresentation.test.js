const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const root = path.join(__dirname, '..')
const read = file => fs.readFileSync(path.join(root, file), 'utf8')

test('every page inlines the complete loading mark before mounting the route transition', () => {
  const app = JSON.parse(read('app.json'))
  assert.equal(app.usingComponents['page-transition'], '/components/page-transition/index')
  assert.equal(app.pages.length, 21)

  const firstPaint = read('components/page-transition/first-paint.wxml')
  assert.match(firstPaint, /class="first-route-veil"/)
  assert.match(firstPaint, /assets\/icons\/spark\.png/)
  assert.equal((firstPaint.match(/<view><\/view>/g) || []).length, 3)

  for (const page of app.pages) {
    const view = read(`${page}.wxml`)
    assert.match(
      view,
      /^<include src="\/components\/page-transition\/first-paint\.wxml"\/>\r?\n<page-transition\/>/,
      `${page} must inline the loading mark before mounting page-transition`
    )
  }
})

test('route transition is short, replayable and uses the HealthMate mark', () => {
  const script = read('components/page-transition/index.js')
  const view = read('components/page-transition/index.wxml')
  const style = read('components/page-transition/index.wxss')

  assert.match(script, /const HOLD_MS = 320/)
  assert.match(script, /const FADE_MS = 180/)
  assert.match(script, /data:[\s\S]*leaving: true/)
  assert.match(script, /data:[\s\S]*paused: true/)
  assert.match(script, /pageLifetimes:[\s\S]*show\(\)[\s\S]*hasShownOnce[\s\S]*this\.play\(\)/)
  assert.doesNotMatch(script, /attached\(\)\s*\{\s*this\.play\(\)/)
  assert.match(script, /hide\(\)[\s\S]*leaving: false/)
  assert.doesNotMatch(view, /wx:if/, 'cached-page veil must stay mounted')
  assert.match(view, /assets\/icons\/spark\.png/)
  assert.equal((view.match(/<view><\/view>/g) || []).length, 3)
  assert.match(style, /\.route-veil\s*\{[^}]*position:\s*fixed;[^}]*transition:\s*opacity \.18s ease-out;/)
  assert.match(style, /@keyframes routeBrandIn/)
  assert.match(style, /@keyframes routeLogoThink/)
  assert.match(style, /@keyframes routeThinkingDot/)
  assert.match(style, /\.route-veil\.paused[\s\S]*animation-play-state: paused/)
})

test('route transition stays mounted, pre-arms while hidden and pauses while idle', () => {
  const componentPath = path.join(root, 'components', 'page-transition', 'index.js')
  const originalComponent = global.Component
  const originalSetTimeout = global.setTimeout
  const originalClearTimeout = global.clearTimeout
  const timers = []
  let definition

  try {
    global.Component = options => { definition = options }
    delete require.cache[require.resolve(componentPath)]
    require(componentPath)

    global.setTimeout = (callback, delay) => {
      timers.push({ callback, delay, cleared: false })
      return timers.length
    }
    global.clearTimeout = id => {
      if (timers[id - 1]) timers[id - 1].cleared = true
    }

    const state = { ...definition.data }
    const instance = {
      data: state,
      setData(patch) { Object.assign(state, patch) }
    }
    Object.assign(instance, definition.methods)

    definition.lifetimes.attached.call(instance)
    definition.pageLifetimes.show.call(instance)
    assert.equal(timers.length, 0, 'compile-time overlay owns the first show')

    definition.pageLifetimes.show.call(instance)
    assert.deepEqual(timers.map(timer => timer.delay), [320, 500])
    timers[0].callback()
    assert.equal(state.leaving, true)
    timers[1].callback()
    assert.deepEqual(state, { leaving: true, paused: true })

    definition.pageLifetimes.show.call(instance)
    definition.pageLifetimes.hide.call(instance)
    assert.deepEqual(state, { leaving: false, paused: true })
    assert.ok(timers.slice(2).every(timer => timer.cleared))

    const timerCount = timers.length
    definition.pageLifetimes.show.call(instance)
    assert.deepEqual(timers.slice(timerCount).map(timer => timer.delay), [320, 500])
    assert.equal(state.leaving, false, 'cached page is already covered before show runs')
    assert.equal(state.paused, false, 'thinking animation resumes only while visible')
  } finally {
    global.Component = originalComponent
    global.setTimeout = originalSetTimeout
    global.clearTimeout = originalClearTimeout
    delete require.cache[require.resolve(componentPath)]
  }
})

test('all page content stays hidden until the inlined first-paint overlay fades', () => {
  const appStyle = read('app.wxss')
  const app = JSON.parse(read('app.json'))

  assert.match(appStyle, /\.first-route-veil\s*\{[^}]*position:fixed;[^}]*z-index:10000;[^}]*background:#f7f7f2;[^}]*animation:firstRouteVeilOut \.18s \.32s ease-out forwards;/)
  assert.match(appStyle, /\.first-route-logo\s*\{[^}]*animation:firstRouteLogoThink \.6s ease-in-out 1;/)
  assert.match(appStyle, /\.first-route-thinking view\s*\{[^}]*animation:firstRouteThinkingDot \.6s ease-in-out 1;/)
  assert.match(appStyle, /\.ui-page-content\s*\{[^}]*animation:\s*pageContentReveal \.36s \.32s cubic-bezier\(\.2,\.8,\.2,1\) backwards;/)
  assert.match(appStyle, /@keyframes firstRouteVeilOut\s*\{\s*from\s*\{opacity:1;\s*\}\s*to\s*\{opacity:0;\s*\}\s*\}/)
  assert.doesNotMatch(appStyle, /page-first-paint-guard|firstPaintGuardOut/)
  assert.match(appStyle, /@keyframes pageContentReveal\s*\{[^}]*opacity:0;[^}]*translateY\(12rpx\)/)
  assert.doesNotMatch(appStyle, /page\s*\{[^}]*animation:/)
  for (const page of app.pages) assert.match(read(`${page}.wxml`), /class="[^"]*\bui-page-content\b/)
})
