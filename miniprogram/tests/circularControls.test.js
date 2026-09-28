const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const root = path.join(__dirname, '..')
const read = file => fs.readFileSync(path.join(root, file), 'utf8')
const escapeRe = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

function ruleBody(css, selector) {
  const match = css.match(new RegExp(`${escapeRe(selector)}\\s*\\{([^}]*)\\}`))
  assert.ok(match, `${selector} must have a style rule`)
  return match[1]
}

function property(body, name) {
  const match = body.match(new RegExp(`(?:^|;)\\s*${name}\\s*:\\s*([^;}]+)`))
  return match && match[1].trim()
}

function hasFixedCircle(file, className) {
  const view = read(file)
  return [...view.matchAll(/class="([^"]+)"/g)].some(([, classes]) => {
    const tokens = classes.split(/\s+/)
    return tokens.includes(className) && tokens.includes('ui-fixed-circle')
  })
}

test('round icon surfaces opt into the non-stretch circle contract', () => {
  const appStyle = read('app.wxss')
  const contract = ruleBody(appStyle, '.ui-fixed-circle')
  assert.equal(property(contract, 'box-sizing'), 'border-box')
  assert.equal(property(contract, 'flex'), 'none')
  assert.equal(property(contract, 'overflow'), 'hidden')
  assert.equal(property(contract, 'border-radius'), '999rpx')

  const surfaces = [
    ['pages/chat/index.wxml', 'icon-action'],
    ['pages/chat/index.wxml', 'icon-copy'],
    ['pages/chat/index.wxml', 'composer-action'],
    ['pages/home/index.wxml', 'hero-orb'],
    ['pages/home/index.wxml', 'sheet-close'],
    ['pages/home/index.wxml', 'si-orb'],
    ['pages/plan/index.wxml', 'del-btn'],
    ['pages/records/index.wxml', 'quick-circle'],
    ['pages/profile/index.wxml', 'avatar-shell'],
    ['pages/profile/index.wxml', 'edit-orb'],
    ['pages/profile/index.wxml', 'feature-orb'],
    ['pages/profile/index.wxml', 'feature-arrow'],
    ['pages/profile/index.wxml', 'mini-orb'],
    ['pages/profile/index.wxml', 'utility-orb'],
    ['pages/goals/index.wxml', 'gicon'],
    ['pages/media/index.wxml', 'upload-icon'],
    ['pages/report/index.wxml', 'ai-orb'],
    ['pages/scan/index.wxml', 'lens'],
    ['pages/settings/privacy/index.wxml', 'shield']
  ]

  for (const [file, className] of surfaces) {
    assert.ok(hasFixedCircle(file, className), `${file}: .${className} must use ui-fixed-circle`)
  }
})

test('audited circle surfaces declare equal width and height', () => {
  const surfaces = [
    ['app.wxss', '.ui-icon-box', '64rpx'],
    ['app.wxss', '.ui-round-button', '60rpx'],
    ['pages/chat/index.wxss', '.icon-action', '60rpx'],
    ['pages/chat/index.wxss', '.icon-copy', '52rpx'],
    ['pages/chat/index.wxss', '.composer-action', '64rpx'],
    ['pages/home/index.wxss', '.hero-orb', '56rpx'],
    ['pages/home/index.wxss', '.si-orb', '80rpx'],
    ['pages/plan/index.wxss', '.del-btn', '52rpx'],
    ['pages/records/index.wxss', '.quick-circle', '82rpx'],
    ['pages/profile/index.wxss', '.avatar-shell', '128rpx'],
    ['pages/profile/index.wxss', '.edit-orb', '56rpx'],
    ['pages/profile/index.wxss', '.feature-orb', '64rpx'],
    ['pages/profile/index.wxss', '.feature-arrow', '48rpx'],
    ['pages/profile/index.wxss', '.mini-orb', '56rpx'],
    ['pages/profile/index.wxss', '.utility-orb', '72rpx'],
    ['pages/goals/index.wxss', '.gicon', '58rpx'],
    ['pages/media/index.wxss', '.upload-icon', '82rpx'],
    ['pages/report/index.wxss', '.ai-orb', '62rpx'],
    ['pages/scan/index.wxss', '.lens', '116rpx'],
    ['pages/settings/privacy/index.wxss', '.shield', '70rpx']
  ]

  for (const [file, selector, size] of surfaces) {
    const body = ruleBody(read(file), selector)
    assert.equal(property(body, 'width'), size, `${file}: ${selector} width`)
    assert.equal(property(body, 'height'), size, `${file}: ${selector} height`)
  }
})

test('copy actions are compact views and cannot grow inside metadata rows', () => {
  const view = read('pages/chat/index.wxml')
  const style = read('pages/chat/index.wxss')
  const copy = ruleBody(style, '.icon-copy')

  assert.equal((view.match(/<view class="icon-copy\b/g) || []).length, 2)
  assert.doesNotMatch(view, /<button[^>]*class="[^"]*\bicon-copy\b/)
  for (const name of ['width', 'min-width', 'max-width', 'height', 'min-height', 'max-height']) {
    assert.equal(property(copy, name), '52rpx', `.icon-copy ${name}`)
  }
  assert.equal(property(copy, 'flex'), '0 0 52rpx')
})
